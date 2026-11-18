"""Auth routes: setup, login, signup, password reset, logout (SECURITY §3).

Thin handlers only: hashing, sessions, rate limiting and the enforcement
itself live in ``app/auth.py`` + ``app/services/users.py``; the policy in
``app/services/authz.py``.

Every form is CSRF-protected (``GET`` seeds the double-submit cookie);
successful login deletes the ``csrf_token`` cookie so the next page load mints
a fresh one ("token rotation on login"). Credential endpoints are rate
limited (5 attempts / 60s per IP+subject).

Owner decision 2026-11-18: full accounts + roles replaced the access-token
credential; ``APP_ACCESS_TOKEN`` remains only the gate switch.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    check_rate_limit,
    clear_session_cookie,
    client_key,
    create_session,
    gate_enabled,
    reset_rate_limit,
    revoke_session,
    revoke_user_sessions,
    set_session_cookie,
)
from app.config import BASE_DIR, settings
from app.csrf import csrf_field, csrf_token
from app.db import get_db
from app.models import User
from app.services import users as users_service

router = APIRouter(tags=["auth"])

# Self-contained template env so the auth pages do not depend on the page
# router's context (base.html renders flash/nav chrome that is meaningless
# behind the gate); only the CSRF helpers are shared, read-only.
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
templates.env.globals["csrf_field"] = csrf_field
templates.env.globals["csrf_token"] = csrf_token

_DEFAULT_NEXT = "/"

#: Longest ``next`` we are willing to render/redirect to.
_MAX_NEXT_LENGTH = 2048

#: Reason slugs the login page turns into friendly copy.
_REASONS = {
    "expired": "Your session expired. Sign in again to continue.",
    "disabled": "This account has been disabled.",
    "reset": "Password updated — sign in with your new password.",
}


def _safe_next(raw: str | None) -> str:
    """Return a local path for ``next`` or ``/`` — never an open redirect."""
    if not raw:
        return _DEFAULT_NEXT
    candidate = raw.strip()
    if len(candidate) > _MAX_NEXT_LENGTH:
        return _DEFAULT_NEXT
    if not candidate.startswith("/") or candidate.startswith("//"):
        return _DEFAULT_NEXT
    if "\\" in candidate or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        return _DEFAULT_NEXT
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc:
        return _DEFAULT_NEXT
    return candidate


def _render(
    request: Request,
    name: str,
    *,
    status_code: int = 200,
    **context: object,
) -> HTMLResponse:
    context.setdefault("app_name", settings.app_name)
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )


def _issue_session(response: RedirectResponse, db: Session, user: User) -> None:
    """Create the session row and drop the pre-login CSRF token."""
    session = create_session(db, user.id)
    db.commit()
    set_session_cookie(response, session.token)
    response.delete_cookie(CSRF_COOKIE_NAME, path="/", httponly=True, samesite="strict")


# ---------------------------------------------------------------------------
# First run: create the owner account
# ---------------------------------------------------------------------------


@router.get("/setup", response_class=HTMLResponse, response_model=None)
def setup_form(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Create the owner account (only while the users table is empty)."""
    if not gate_enabled():
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    if db.execute(select(User.id).limit(1)).first():
        return RedirectResponse("/login", status_code=303)
    return _render(
        request,
        "setup.html",
        next_url=_safe_next(request.query_params.get("next")),
        error=None,
    )


@router.post("/setup", response_class=HTMLResponse, response_model=None)
def setup_submit(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
    next_url: str = Form("", alias="next"),
) -> HTMLResponse | RedirectResponse:
    """Validate + create the first account as owner, then sign in."""
    if not gate_enabled():
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    if db.execute(select(User.id).limit(1)).first():
        return RedirectResponse("/login", status_code=303)
    if not check_rate_limit(client_key(request, "setup")):
        return _render(
            request,
            "setup.html",
            next_url=_safe_next(next_url),
            error="Too many attempts. Wait a minute and try again.",
            status_code=429,
        )
    if password != confirm:
        return _render(
            request,
            "setup.html",
            next_url=_safe_next(next_url),
            error="The two passwords do not match.",
        )
    try:
        owner = users_service.create_user(db, username=username, password=password, role="owner")
    except ValueError as exc:
        return _render(
            request,
            "setup.html",
            next_url=_safe_next(next_url),
            error=str(exc),
        )
    reset_rate_limit(client_key(request, "setup"))
    response = RedirectResponse(_safe_next(next_url), status_code=303)
    _issue_session(response, db, owner)
    return response


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    """Render the account sign-in form."""
    if not gate_enabled():
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    # /login is allowlisted (the middleware skips it), so the session row is
    # checked directly: an active session bounces back to the app.
    from app.auth import lookup_session

    if lookup_session(db, request.cookies.get(SESSION_COOKIE_NAME)) is not None:
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    if not db.execute(select(User.id).limit(1)).first():
        return RedirectResponse("/setup", status_code=303)
    reason = request.query_params.get("reason") or ""
    # "reset" is good news (password updated); expired/disabled are errors.
    notice = _REASONS.get(reason) if reason == "reset" else None
    error = None if reason == "reset" else _REASONS.get(reason)
    return _render(
        request,
        "login.html",
        next_url=_safe_next(request.query_params.get("next")),
        error=error,
        notice=notice,
        allow_signup=settings.allow_signup,
    )


@router.post("/login", response_class=HTMLResponse, response_model=None)
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(""),
    password: str = Form(""),
    next_url: str = Form("", alias="next"),
) -> HTMLResponse | RedirectResponse:
    """Authenticate username+password and issue a server-side session."""
    if not gate_enabled():
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    key = client_key(request, username)
    if not check_rate_limit(key):
        return _render(
            request,
            "login.html",
            next_url=_safe_next(next_url),
            error="Too many attempts. Wait a minute and try again.",
            allow_signup=settings.allow_signup,
            status_code=429,
        )
    user = users_service.authenticate(db, username, password)
    if user is None:
        # Same copy for unknown user and wrong password (no enumeration).
        return _render(
            request,
            "login.html",
            next_url=_safe_next(next_url),
            error="Invalid username or password.",
            allow_signup=settings.allow_signup,
            status_code=401,
        )
    if user.disabled:
        return _render(
            request,
            "login.html",
            next_url=_safe_next(next_url),
            error="This account has been disabled.",
            allow_signup=settings.allow_signup,
            status_code=403,
        )
    reset_rate_limit(key)
    response = RedirectResponse(_safe_next(next_url), status_code=303)
    _issue_session(response, db, user)
    return response


# ---------------------------------------------------------------------------
# Sign up (env-gated; the first account always comes from /setup)
# ---------------------------------------------------------------------------


@router.get("/signup", response_class=HTMLResponse, response_model=None)
def signup_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    if not gate_enabled() or not settings.allow_signup:
        return RedirectResponse("/login", status_code=303)
    if not db.execute(select(User.id).limit(1)).first():
        return RedirectResponse("/setup", status_code=303)
    return _render(request, "signup.html", error=None)


@router.post("/signup", response_class=HTMLResponse, response_model=None)
def signup_submit(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    """Open registration creates a member (ALLOW_SIGNUP=true only)."""
    if not gate_enabled() or not settings.allow_signup:
        return RedirectResponse("/login", status_code=303)
    if not check_rate_limit(client_key(request, "signup")):
        return _render(
            request,
            "signup.html",
            error="Too many attempts. Wait a minute and try again.",
            status_code=429,
        )
    if password != confirm:
        return _render(request, "signup.html", error="The two passwords do not match.")
    try:
        member = users_service.create_user(db, username=username, password=password, role="member")
    except ValueError as exc:
        return _render(request, "signup.html", error=str(exc))
    reset_rate_limit(client_key(request, "signup"))
    response = RedirectResponse(_DEFAULT_NEXT, status_code=303)
    _issue_session(response, db, member)
    return response


# ---------------------------------------------------------------------------
# Forgot / reset password (local-first: the link is shown on screen)
# ---------------------------------------------------------------------------


@router.get("/forgot-password", response_class=HTMLResponse, response_model=None)
def forgot_password_form(request: Request) -> HTMLResponse | RedirectResponse:
    if not gate_enabled():
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    return _render(request, "forgot-password.html", error=None, link=None, username=None)


@router.post("/forgot-password", response_class=HTMLResponse, response_model=None)
def forgot_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    """Issue a single-use reset token; the local instance shows the link.

    No email service exists in a local-first app (documented tradeoff: the
    page differs only by the link, so usernames are enumerable here — rate
    limited to 5/hour per client, and the same limit guards /login).
    """
    if not gate_enabled():
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    if not check_rate_limit(client_key(request, "forgot")):
        return _render(
            request,
            "forgot-password.html",
            error="Too many attempts. Wait a minute and try again.",
            link=None,
            username=None,
            status_code=429,
        )
    user = users_service.get_user_by_username(db, username)
    link = None
    if user is not None and not user.disabled:
        token = users_service.issue_reset_token(db, user)
        db.commit()
        link = f"/reset-password?token={token}"
    # Same page either way; only the created link reveals the account exists.
    return _render(
        request,
        "forgot-password.html",
        error=None,
        link=link,
        username=(user.username if user else None),
    )


@router.get("/reset-password", response_class=HTMLResponse, response_model=None)
def reset_password_form(request: Request) -> HTMLResponse | RedirectResponse:
    if not gate_enabled():
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    token = request.query_params.get("token", "")
    return _render(
        request,
        "reset-password.html",
        token=token,
        error=None,
    )


@router.post("/reset-password", response_class=HTMLResponse, response_model=None)
def reset_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    """Consume the reset token, set the password, kill every session."""
    if not gate_enabled():
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    if not check_rate_limit(client_key(request, "reset")):
        return _render(
            request,
            "reset-password.html",
            token=token,
            error="Too many attempts. Wait a minute and try again.",
            status_code=429,
        )
    if password != confirm:
        return _render(
            request, "reset-password.html", token=token, error="The two passwords do not match."
        )
    try:
        user = users_service.consume_reset_token(db, token, password)
    except ValueError as exc:
        return _render(request, "reset-password.html", token=token, error=str(exc))
    if user is None:
        return _render(
            request,
            "reset-password.html",
            token=token,
            error="This reset link is invalid or has expired. Request a new one.",
            status_code=400,
        )
    revoke_user_sessions(db, user.id)
    db.commit()
    return RedirectResponse("/login?reason=reset", status_code=303)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.post("/logout", response_class=HTMLResponse, response_model=None)
def logout(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Drop the session cookie AND revoke the row (server-side logout)."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    revoke_session(db, token)
    db.commit()
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


@router.post("/logout-all", response_class=HTMLResponse, response_model=None)
def logout_all(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Revoke every session of the signed-in user (Account page)."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        revoke_user_sessions(db, user_id)
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response
