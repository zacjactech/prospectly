"""Login routes for the optional access-token gate (SECURITY_AND_PRIVACY §3).

Thin handlers only: token comparison, session issuing and cookie rotation live
here; the enforcement itself is ``app.auth.AuthMiddleware``.

``POST /login`` is deliberately *not* exempt from CSRF — ``GET /login`` seeds
the double-submit cookie, so the form carries ``{{ csrf_field() }}`` and the
existing middleware validates it. Successful login deletes the ``csrf_token``
cookie so the next page load mints a fresh one ("token rotation on login").
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_IDLE_SECONDS,
    gate_enabled,
    sign_session,
)
from app.config import BASE_DIR, settings
from app.csrf import csrf_field, csrf_token

router = APIRouter(tags=["auth"])

# Self-contained template env so the login page does not depend on the page
# router's context (base.html renders flash/nav chrome that is meaningless
# behind the gate); only the CSRF helpers are shared, read-only.
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
templates.env.globals["csrf_field"] = csrf_field
templates.env.globals["csrf_token"] = csrf_token

_DEFAULT_NEXT = "/"

#: Longest ``next`` we are willing to render/redirect to.
_MAX_NEXT_LENGTH = 2048


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


def _render_login(
    request: Request, *, next_url: str, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "app_name": settings.app_name,
            "next": next_url,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_form(request: Request) -> HTMLResponse | RedirectResponse:
    """Render the token form (no-op redirect when the gate is disabled)."""
    if not gate_enabled():
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)
    next_url = _safe_next(request.query_params.get("next"))
    return _render_login(request, next_url=next_url)


@router.post("/login", response_class=HTMLResponse, response_model=None)
def login_submit(
    request: Request,
    access_token: str = Form(""),
    next_url: str = Form("", alias="next"),
) -> HTMLResponse | RedirectResponse:
    """Validate the token and issue a signed session cookie."""
    expected = settings.app_access_token
    if not expected:
        # Gate switched off between rendering and submitting: nothing to do.
        return RedirectResponse(_DEFAULT_NEXT, status_code=303)

    candidate = access_token.strip()
    # Compare bytes: ``compare_digest`` raises TypeError on non-ASCII ``str``,
    # so a pasted accented character must yield 401, not 500.
    if not candidate or not secrets.compare_digest(
        candidate.encode("utf-8"), expected.encode("utf-8")
    ):
        # 401 with a rendered form (no session issued, no redirect loop).
        return _render_login(
            request,
            next_url=_safe_next(next_url),
            error="Invalid access token.",
            status_code=401,
        )

    response = RedirectResponse(_safe_next(next_url), status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        sign_session(int(time.time()) + SESSION_IDLE_SECONDS),
        httponly=True,
        samesite="strict",
        path="/",
        secure=False,  # loopback HTTP in MVP; set True behind TLS/LAN
    )
    # Rotation on login: drop the pre-login CSRF token; the middleware re-seeds
    # a fresh one on the next page load.
    response.delete_cookie(CSRF_COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response


@router.post("/logout", response_class=HTMLResponse, response_model=None)
def logout() -> RedirectResponse:
    """Drop the session cookie (gated: only an authenticated session gets here)."""
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response
