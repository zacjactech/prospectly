"""CSRF protection (SECURITY_AND_PRIVACY.md §3).

Double-submit cookie pattern:

* A ``csrf_token`` cookie is set on every response (HttpOnly, SameSite=Strict).
* State-changing requests (POST/PATCH/PUT/DELETE) must present the same token
  either as the ``X-CSRF-Token`` header or as a ``csrf_token`` form field.
* The middleware compares the cookie value to the presented token; mismatch or
  absence ⇒ 403.

GET/HEAD/OPTIONS are exempt (read-only). The token is a random 32-byte hex
string generated once per session and regenerated on login (login not yet in
MVP; cookie lives until browser clears it).

HTML forms include the token via the ``csrf_field()`` Jinja2 global. JSON clients
read the cookie and send it back as ``X-CSRF-Token``.
"""

from __future__ import annotations

import secrets

from fastapi import Request, Response
from jinja2 import pass_context
from markupsafe import Markup
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from starlette.types import Message

# Methods that mutate state and therefore require a CSRF token.
_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})

_COOKIE_NAME = "csrf_token"
_HEADER_NAME = "X-CSRF-Token"
_FORM_FIELD_NAME = "csrf_token"


def _new_token() -> str:
    return secrets.token_hex(32)


def _get_cookie_token(request: Request) -> str | None:
    return request.cookies.get(_COOKIE_NAME)


def csrf_token(request: Request) -> str:
    """Return the current CSRF token for the request (cookie value).

    Used by templates via ``csrf_field()`` and by JSON clients reading the
    cookie. Returns a fresh token if none exists yet.
    """
    token = _get_cookie_token(request)
    if token is None:
        token = _new_token()
    return token


@pass_context
def csrf_field(context: dict) -> Markup:
    """Render a hidden input field carrying the CSRF token for HTML forms."""
    request: Request = context["request"]
    token = csrf_token(request)
    return Markup(f'<input type="hidden" name="{_FORM_FIELD_NAME}" value="{token}">')


def _tokens_match(cookie_token: str | None, presented_token: str | None) -> bool:
    if not cookie_token or not presented_token:
        return False
    return secrets.compare_digest(cookie_token, presented_token)


def _extract_form_token(body: bytes, content_type: str) -> str | None:
    """Extract the csrf_token field from a URL-encoded form body."""
    if "application/x-www-form-urlencoded" not in content_type:
        return None
    from urllib.parse import parse_qs

    parsed = parse_qs(body.decode("utf-8", errors="replace"))
    tokens = parsed.get(_FORM_FIELD_NAME)
    if tokens:
        return tokens[0]
    return None


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests that lack a matching CSRF token."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in _MUTATING_METHODS:
            cookie_token = _get_cookie_token(request)

            # Read + cache the body so downstream form()/json() parsing still
            # works, and extract the csrf_token field if it's a form post.
            body = await request.body()
            presented_token = _extract_form_token(body, request.headers.get("content-type", ""))
            # Header takes precedence (JSON clients).
            header_token = request.headers.get(_HEADER_NAME)
            presented_token = header_token or presented_token

            # Re-wrap the receive channel so downstream code can re-read the body.
            async def receive() -> Message:
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }

            request._receive = receive

            if not _tokens_match(cookie_token, presented_token):
                return JSONResponse(
                    {
                        "code": "csrf_failure",
                        "message": "Missing or invalid CSRF token",
                    },
                    status_code=403,
                )

        response = await call_next(request)

        # Set/refresh the csrf_token cookie on every response so the client
        # always has a current token for the next mutating request.
        cookie_token = _get_cookie_token(request)
        if cookie_token is None:
            response.set_cookie(
                _COOKIE_NAME,
                _new_token(),
                httponly=True,
                samesite="strict",
                path="/",
                secure=False,  # localhost-only in MVP; set True behind TLS/LAN
            )
        return response
