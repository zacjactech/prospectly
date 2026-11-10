"""Optional access-token gate (SECURITY_AND_PRIVACY.md §3, API_DESIGN.md §4).

Behaviour:

* ``APP_ACCESS_TOKEN`` unset (default, loopback MVP) ⇒ the gate is inert; every
  route behaves exactly as without it.
* ``APP_ACCESS_TOKEN`` set ⇒ every request needs a valid session cookie, except
  the login page, ``/healthz`` and static assets. API/JSON requests get
  ``401 {"detail": ...}``; HTML requests get a ``302`` to ``/login?next=...``.

Session cookie format (stdlib only, no new dependency)::

    <expiry_unix>.<hmac_sha256_hex>

where the MAC covers the ``<expiry_unix>`` string and is keyed with the access
token. Rotating ``APP_ACCESS_TOKEN`` therefore invalidates every issued
session. Expiry is a 12h *idle* timeout: authenticated responses re-issue the
cookie with ``now + 12h``.

The cookie is HttpOnly, SameSite=Strict, path ``/``, ``Secure`` off for
loopback HTTP (same flags as ``app/csrf.py``). It carries no user data, only a
signed expiry.

Token and session values are never logged (SECURITY §4).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.config import settings

#: Name of the signed session cookie.
SESSION_COOKIE_NAME = "prospectly_session"

#: Idle timeout for a session (documented: 12h).
SESSION_IDLE_SECONDS = 12 * 60 * 60

#: CSRF cookie name, mirrored from ``app/csrf.py`` (read-only use: the login
#: handler deletes it so the middleware re-seeds a fresh token after login).
CSRF_COOKIE_NAME = "csrf_token"

#: Paths that never require a session, whatever the method.
_ALLOWLIST_EXACT = frozenset({"/login", "/healthz"})

#: Prefix allowlist (static assets, mounted with Starlette).
_ALLOWLIST_PREFIXES = ("/static/",)

#: Upper bound on a presented cookie value; anything longer is garbage.
_MAX_COOKIE_LENGTH = 128

_UNAUTHORIZED_DETAIL = "Missing or invalid access token. Sign in at /login to obtain a session."


def gate_enabled() -> bool:
    """True when an access token is configured and the gate must apply."""
    return bool(settings.app_access_token)


def _signing_key() -> bytes | None:
    token = settings.app_access_token
    return token.encode("utf-8") if token else None


def sign_session(expiry: int) -> str:
    """Sign ``expiry`` (unix seconds) into a session cookie value."""
    key = _signing_key()
    if key is None:
        raise RuntimeError("Cannot sign a session without APP_ACCESS_TOKEN")
    stamp = str(int(expiry))
    digest = hmac.new(key, stamp.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{stamp}.{digest}"


def verify_session(value: str | None, *, now: float | None = None) -> int | None:
    """Return the session expiry if ``value`` is authentic and unexpired.

    Never raises: malformed, forged, or expired cookies all return ``None``.
    """
    key = _signing_key()
    if key is None or not value or len(value) > _MAX_COOKIE_LENGTH:
        return None

    stamp, sep, digest = value.partition(".")
    # ``isascii`` guards the ``encode``/``compare_digest`` calls below: both
    # reject (or raise on) non-ASCII text, which a raw latin-1 cookie can carry.
    if not sep or not digest or not stamp.isascii() or not stamp.isdigit() or not digest.isascii():
        return None

    try:
        expiry = int(stamp)
    except ValueError:  # pragma: no cover - guarded by str.isdigit()
        return None
    if expiry <= 0:
        return None

    expected = hmac.new(key, stamp.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, digest):
        return None

    current = time.time() if now is None else now
    if current >= expiry:
        return None
    return expiry


def is_allowlisted(path: str) -> bool:
    """True for the paths that stay reachable without a session."""
    # Normalise a trailing slash so ``/healthz/`` cannot dodge the allowlist
    # into a redirect loop (/login/, /healthz/ → 404 behind the gate).
    normalized = path.rstrip("/") or "/"
    return normalized in _ALLOWLIST_EXACT or path.startswith(_ALLOWLIST_PREFIXES)


def wants_json(request: Request) -> bool:
    """Decide between a JSON 401 and an HTML redirect for a gated request."""
    if request.url.path == "/api" or request.url.path.startswith("/api/"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def login_redirect_url(request: Request) -> str:
    """Build ``/login?next=<path[?query]>`` for the current request."""
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return f"/login?next={quote(target, safe='')}"


def _session_cookie_cleared(response: Response) -> bool:
    """True when the handler itself expired the session (e.g. ``/logout``)."""
    prefix = f"{SESSION_COOKIE_NAME}="
    for header in response.headers.getlist("set-cookie"):
        if not header.startswith(prefix):
            continue
        value = header.split(";", 1)[0][len(prefix) :]
        if value in {"", '""'} or "max-age=0" in header.lower():
            return True
    return False


def _refresh_session_cookie(response: Response) -> None:
    """Slide the idle timeout: re-issue the cookie with ``now + 12h``."""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        sign_session(int(time.time()) + SESSION_IDLE_SECONDS),
        httponly=True,
        samesite="strict",
        path="/",
        secure=False,  # loopback HTTP in MVP; set True behind TLS/LAN
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce the access-token session on every non-allowlisted request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Token unset ⇒ no gate at all (loopback MVP, all existing routes intact).
        if not gate_enabled() or is_allowlisted(request.url.path):
            return await call_next(request)

        expiry = verify_session(request.cookies.get(SESSION_COOKIE_NAME))
        if expiry is None:
            if wants_json(request):
                return JSONResponse({"detail": _UNAUTHORIZED_DETAIL}, status_code=401)
            return RedirectResponse(login_redirect_url(request), status_code=302)

        request.state.authenticated = True
        request.state.session_expiry = expiry
        response = await call_next(request)
        # A handler that explicitly expired the cookie (logout) wins.
        if not _session_cookie_cleared(response):
            _refresh_session_cookie(response)
        return response
