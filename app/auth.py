"""Authentication gate, revocable sessions and permission dependencies
(SECURITY_AND_PRIVACY §3, amended 2026-11-18 for full accounts + roles).

Behaviour:

* ``APP_ACCESS_TOKEN`` unset (default, loopback MVP) ⇒ the gate is inert; every
  route behaves exactly as without it (permissions pass through too).
* ``APP_ACCESS_TOKEN`` set ⇒ the token is only the on/off switch (plus the
  non-loopback startup guard). Credentials live in the ``users`` table
  (scrypt-hashed passwords): ``GET/POST /login`` authenticates and issues an
  **opaque DB-backed session** (``user_sessions`` row), so logout, logout-all,
  disable-user and password-reset all revoke server-side.

Session cookie: ``secrets.token_urlsafe(32)`` (no claims in the value),
``HttpOnly; SameSite=Strict; Path=/``; ``Secure`` follows
``settings.cookie_secure`` (False for loopback HTTP; set True behind TLS).
The idle timeout is a 12h **sliding** row expiry (``expires_at`` extended on
authenticated responses when under an hour remains) — rotating
``APP_ACCESS_TOKEN`` no longer matters for sessions; disabling the user does.

First run with the gate on and an empty ``users`` table: every path redirects
to ``GET/POST /setup`` (create the owner account), then login. ``/signup`` is
env-gated (``ALLOW_SIGNUP``, default off). Login/forgot/reset are rate limited
(5 attempts / 60s per IP+subject, in-memory) — closes the documented
"no rate limiting on /login" gap.

Allowlist (never need a session): ``/login``, ``/signup``, ``/setup``,
``/forgot-password``, ``/reset-password``, ``/healthz``, ``/static/*``.

HTML requests get ``302 /login?next=<local path>[&reason=…]``; API/JSON get
``401 {"detail": …}``. Values are never logged (SECURITY §4).
"""

from __future__ import annotations

import datetime as dt
import secrets
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.config import settings
from app.db import SessionLocal
from app.models import User, UserSession
from app.services import authz

#: Name of the opaque session cookie (value format changed2026-11-18).
SESSION_COOKIE_NAME = "prospectly_session"

#: Idle timeout for a session (documented: 12h sliding).
SESSION_IDLE_SECONDS = 12 * 60 * 60

#: Re-issue (extend the row) when less than this much of the idle window
#: remains — at most one UPDATE per session per hour.
SESSION_SLIDE_AFTER = 60 * 60

#: CSRF cookie name, mirrored from ``app/csrf.py`` (the login handler deletes
#: it so the middleware re-seeds a fresh token after login).
CSRF_COOKIE_NAME = "csrf_token"

#: Paths that never require a session, whatever the method.
_ALLOWLIST_EXACT = frozenset(
    {"/login", "/signup", "/setup", "/forgot-password", "/reset-password", "/healthz"}
)

#: Prefix allowlist (static assets, mounted with Starlette).
_ALLOWLIST_PREFIXES = ("/static/",)

#: Upper bound on a presented cookie value; anything longer is garbage.
_MAX_TOKEN_LENGTH = 128

#: Rate limit for credential endpoints: attempts per key per window.
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60.0

_UNAUTHORIZED_DETAIL = "Missing or invalid session. Sign in at /login to continue."

# In-memory rate-limit buckets: key -> attempt timestamps (monotonic seconds).
_rate_buckets: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def gate_enabled() -> bool:
    """True when the access token is configured and the gate must apply."""
    return bool(settings.app_access_token)


# ---------------------------------------------------------------------------
# Rate limiting (SECURITY §3: closes the documented "no rate limiting" gap)
# ---------------------------------------------------------------------------


def check_rate_limit(key: str, *, limit: int = RATE_LIMIT_ATTEMPTS) -> bool:
    """Record an attempt for ``key``; False when the window is exhausted."""
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, [])
        bucket[:] = [t for t in bucket if now - t <= RATE_LIMIT_WINDOW_SECONDS]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def reset_rate_limit(key: str) -> None:
    """Clear a bucket (successful login forgets the failures)."""
    with _rate_lock:
        _rate_buckets.pop(key, None)


def reset_all_rate_limits() -> None:
    """Drop every bucket (used by tests for isolation)."""
    with _rate_lock:
        _rate_buckets.clear()


def client_key(request: Request, subject: str) -> str:
    """Rate-limit key: client IP + subject (username or 'reset')."""
    ip = request.client.host if request.client else "unknown"
    return f"{ip}:{subject.strip().lower()}"


# ---------------------------------------------------------------------------
# Session store (opaque cookie -> user_sessions row)
# ---------------------------------------------------------------------------


def create_session(db: DbSession, user_id: int) -> UserSession:
    """Issue a new server-side session; caller commits."""
    now = dt.datetime.now(dt.UTC)
    session = UserSession(
        token=secrets.token_urlsafe(32),
        user_id=user_id,
        expires_at=now + dt.timedelta(seconds=SESSION_IDLE_SECONDS),
    )
    db.add(session)
    db.flush()
    return session


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def lookup_session(db: DbSession, token: str | None) -> UserSession | None:
    """Return the live session row for ``token`` (expired rows are purged)."""
    if not token or len(token) > _MAX_TOKEN_LENGTH:
        return None
    session = db.scalar(select(UserSession).where(UserSession.token == token))
    if session is None:
        return None
    if _aware(session.expires_at) < dt.datetime.now(dt.UTC):
        db.delete(session)
        db.flush()
        return None
    return session


def touch_session(db: DbSession, session: UserSession) -> bool:
    """Slide the idle expiry; True when the row was extended."""
    now = dt.datetime.now(dt.UTC)
    remaining = (_aware(session.expires_at) - now).total_seconds()
    if remaining >= SESSION_IDLE_SECONDS - SESSION_SLIDE_AFTER:
        return False
    session.expires_at = now + dt.timedelta(seconds=SESSION_IDLE_SECONDS)
    db.flush()
    return True


def revoke_session(db: DbSession, token: str | None) -> None:
    """Server-side logout for one cookie value; caller commits."""
    if not token:
        return
    db.execute(delete(UserSession).where(UserSession.token == token))
    db.flush()


def revoke_user_sessions(db: DbSession, user_id: int) -> None:
    """Kill every session of a user (logout-all, disable, password reset)."""
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    db.flush()


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        path="/",
        secure=settings.cookie_secure,  # loopback HTTP False; True behind TLS
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


def is_allowlisted(path: str) -> bool:
    """True for the paths that stay reachable without a session."""
    normalized = path.rstrip("/") or "/"
    return normalized in _ALLOWLIST_EXACT or path.startswith(_ALLOWLIST_PREFIXES)


def wants_json(request: Request) -> bool:
    """Decide between a JSON 401 and an HTML redirect for a gated request."""
    if request.url.path == "/api" or request.url.path.startswith("/api/"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def login_redirect_url(request: Request, *, reason: str | None = None) -> str:
    """Build ``/login?next=<path[?query]>[&reason=…]`` for the current request."""
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    url = f"/login?next={quote(target, safe='')}"
    if reason:
        url = f"{url}&reason={reason}"
    return url


def _users_exist(db: DbSession) -> bool:
    return db.execute(select(User.id).limit(1)).first() is not None


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce the account session on every non-allowlisted request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Token unset ⇒ no gate at all (loopback MVP, all existing routes intact).
        if not gate_enabled() or is_allowlisted(request.url.path):
            return await call_next(request)

        db = SessionLocal()
        try:
            # First run: every path funnels into the owner setup page.
            if request.url.path.rstrip("/") == "/setup":
                if _users_exist(db):
                    return RedirectResponse("/login", status_code=302)
            elif not _users_exist(db):
                # First run: the owner account does not exist yet — funnel to
                # setup (the handler re-checks and redirects once created).
                # API/JSON callers get the JSON contract, never an HTML page.
                if wants_json(request):
                    return JSONResponse(
                        {"detail": "No account exists yet. Create the owner account at /setup."},
                        status_code=401,
                    )
                return RedirectResponse("/setup", status_code=302)

            token = request.cookies.get(SESSION_COOKIE_NAME)
            session = lookup_session(db, token)
            # lookup_session may have purged an expired row (flush only):
            # commit before any early return can roll it back.
            db.commit()
            if session is None:
                reason = "expired" if token else None
                if wants_json(request):
                    return JSONResponse({"detail": _UNAUTHORIZED_DETAIL}, status_code=401)
                return RedirectResponse(login_redirect_url(request, reason=reason), status_code=302)

            user = session.user
            if user.disabled:
                revoke_session(db, token)
                db.commit()
                if wants_json(request):
                    return JSONResponse(
                        {"detail": "This account has been disabled."}, status_code=401
                    )
                disabled_redirect = RedirectResponse("/login?reason=disabled", status_code=302)
                clear_session_cookie(disabled_redirect)
                return disabled_redirect

            # Immutable identity for handlers/deps (the ORM row is detached
            # when this middleware's session closes).
            request.state.user_id = user.id
            request.state.user_username = user.username
            request.state.user_role = user.role
            request.state.authenticated = True

            # Slide the idle expiry BEFORE forwarding the request. Doing it
            # after call_next deadlocks against the handler's own uncommitted
            # write (SQLite has one writer; the handler's commit only runs at
            # dependency teardown, after the response has started): the status
            # card's profile bootstrap INSERT would hold the write lock while
            # our UPDATE waits on it. Touch + commit here releases the writer
            # lock first; a later logout just deletes the row we extended.
            touch_session(db, session)  # sliding idle: row only, no cookie churn
            db.commit()
            return await call_next(request)
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Dependencies for route-level authorization (app/services/authz.py policy)
# ---------------------------------------------------------------------------


def current_user(request: Request) -> SimpleNamespace | None:
    """Lightweight identity for handlers; None when the gate is off."""
    if not gate_enabled():
        return None
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        return None
    return SimpleNamespace(
        id=user_id,
        username=getattr(request.state, "user_username", ""),
        role=getattr(request.state, "user_role", ""),
    )


def require_permission(permission: str) -> Callable[[Request], SimpleNamespace | None]:
    """Route dependency: gate off → pass; otherwise enforce the role policy."""

    def dependency(request: Request) -> SimpleNamespace | None:
        user = current_user(request)
        if user is None:
            # Gate off: loopback MVP has no identity (documented pass-through).
            if not gate_enabled():
                return None
            raise HTTPException(status_code=403, detail="Sign in to continue.")
        if not authz.has_permission(user.role, permission):
            raise HTTPException(
                status_code=403, detail=authz.PERMISSION_MESSAGES.get(permission, "Not allowed.")
            )
        return user

    return dependency


def require_user(request: Request) -> SimpleNamespace | None:
    """Route dependency: any authenticated identity (gate off → pass as None)."""
    return current_user(request)


__all__ = [
    "SESSION_COOKIE_NAME",
    "SESSION_IDLE_SECONDS",
    "CSRF_COOKIE_NAME",
    "AuthMiddleware",
    "gate_enabled",
    "check_rate_limit",
    "reset_rate_limit",
    "reset_all_rate_limits",
    "client_key",
    "create_session",
    "lookup_session",
    "touch_session",
    "revoke_session",
    "revoke_user_sessions",
    "set_session_cookie",
    "clear_session_cookie",
    "is_allowlisted",
    "wants_json",
    "login_redirect_url",
    "current_user",
    "require_permission",
    "require_user",
]
