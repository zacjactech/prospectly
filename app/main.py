"""FastAPI application factory (SYSTEM_ARCHITECTURE §1).

Single process serves: Jinja2 pages (+ HTMX partials later) and the JSON API.
Boundary rules live in AGENTS.md §5 — routes stay thin, services hold the logic.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.auth import AuthMiddleware, gate_enabled
from app.config import BASE_DIR, settings
from app.csrf import CSRFMiddleware, csrf_field, csrf_token
from app.logging_setup import setup_logging
from app.services.activity import describe_payload

logger = logging.getLogger(__name__)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # No inline scripts/styles anywhere in MVP templates (UI spec + CSP, SECURITY §2).
    # The style hash allows exactly one exception: the .htmx-indicator CSS that
    # the vendored (pinned) htmx.min.js injects into <head> — hash verified in
    # browser against the blocked content; update only when re-vendoring htmx.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'sha256-faU7yAF8NxuMTNEwVmBz+VcYeIoBQ2EMHW3WaVxCvnk='; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    ),
}

# Swagger UI and ReDoc inject styles at runtime (styled-components). The two
# docs routes are the only exception: assets are self-hosted, but their
# generated style tags need 'unsafe-inline'. The rest of the app keeps the
# hash-only policy above — this value is applied only to these two paths.
_DOCS_PATHS = frozenset({"/api/docs", "/api/redoc"})
_DOCS_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; worker-src blob:; "
    "frame-ancestors 'none'"
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings.ensure_dirs()
    if not settings.is_loopback and not settings.app_access_token:
        # SECURITY §3: refuse to expose the app beyond loopback without a token.
        raise RuntimeError(
            "BIND_HOST is not loopback but APP_ACCESS_TOKEN is not set. "
            "Set APP_ACCESS_TOKEN or bind to 127.0.0.1."
        )
    logger.info(
        "Starting %s %s (db=%s, ai_provider=%s)",
        settings.app_name,
        settings.app_version,
        settings.database_url.split("://")[0],
        settings.ai_provider,
    )
    yield
    logger.info("%s shut down", settings.app_name)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        # FastAPI's built-in docs HTML embeds an inline init script and loads
        # CDN bundles. We serve equivalent pages from local, pinned assets
        # instead (app/static/vendor/docs), with a route-scoped CSP below.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

    from app.routes import api, auth, pages

    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(api.router, prefix="/api/v1")

    @app.get("/api/docs", response_class=HTMLResponse, include_in_schema=False)
    async def api_docs() -> HTMLResponse:
        """Swagger UI from pinned, self-hosted assets (no CDN under CSP)."""
        return HTMLResponse(
            """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prospectly API docs</title>
<link rel="stylesheet" href="/static/vendor/docs/swagger-ui.css?v=1">
</head>
<body>
<div id="swagger-ui"></div>
<script src="/static/vendor/docs/swagger-ui-bundle.js?v=1"></script>
<script src="/static/js/api-docs-init.js?v=1"></script>
</body>
</html>"""
        )

    @app.get("/api/redoc", response_class=HTMLResponse, include_in_schema=False)
    async def api_redoc() -> HTMLResponse:
        """ReDoc from pinned, self-hosted assets (no CDN under CSP)."""
        return HTMLResponse(
            """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prospectly API reference</title>
</head>
<body>
<redoc spec-url="/api/openapi.json"></redoc>
<script src="/static/vendor/docs/redoc.standalone.js?v=1"></script>
</body>
</html>"""
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
        """HTML form posts get a readable 400 page; JSON keeps FastAPI's 422.

        Required ``Form(...)`` fields are rejected before the route runs, so a
        handler's own ``field_errors`` re-render never gets the chance — the
        browser used to land on a raw ``{"detail":[{"type":"missing"…}]}``
        document with the canvas gone. API/JSON callers keep the 422 contract.
        """
        content_type = request.headers.get("content-type", "")
        accept = request.headers.get("accept", "")
        if (
            request.url.path.startswith("/api/")
            or "application/json" in content_type
            or "application/json" in accept
        ):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        detail = []
        for err in exc.errors():
            location = ".".join(str(part) for part in err.get("loc", ()) if part != "body")
            location = location.replace("_", " ")
            message = str(err.get("msg", "Invalid value"))
            detail.append(f"{location}: {message}" if location else message)
        return pages.templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "app_name": settings.app_name,
                "title": "That form could not be processed",
                "message": (
                    "A required field was missing from the submission. "
                    "Go back and try again. Nothing was saved."
                ),
                "detail": detail,
            },
            status_code=400,
        )

    # CSRF protection (SECURITY_AND_PRIVACY.md §3): double-submit cookie.
    app.add_middleware(CSRFMiddleware)

    # Access-token gate (SECURITY_AND_PRIVACY.md §3). Inert when
    # APP_ACCESS_TOKEN is unset. Starlette builds the stack with the most
    # recently added middleware outermost, so the gate runs *before* CSRF:
    # gated requests are rejected without reading bodies, while the allowlisted
    # POST /login still flows through CSRFMiddleware and is CSRF-checked.
    app.add_middleware(AuthMiddleware)

    # Expose CSRF helpers to Jinja2 templates.
    pages.templates.env.globals["csrf_token"] = csrf_token
    pages.templates.env.globals["csrf_field"] = csrf_field
    # Gate state drives nav visibility (Account link, settings cluster cards).
    pages.templates.env.globals["gate_enabled"] = gate_enabled
    # Activity payloads render as readable text (never raw JSON) in the panels.
    pages.templates.env.filters["describe_payload"] = describe_payload

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
        """HTML requests get the error page; JSON/API keeps {detail} shape.

        Registered for *both* exception classes: FastAPI's HTTPException
        (raised by require_permission and route handlers) subclasses
        Starlette's, but an unmatched route raises only Starlette's — without
        the second registration a dead bookmark answered raw
        {"detail":"Not Found"} to a browser.
        """
        accept = request.headers.get("accept", "")
        is_json = request.url.path.startswith("/api/") or "application/json" in accept
        if is_json or "text/html" not in accept:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        titles = {403: "Not allowed", 404: "Not found", 401: "Sign in required"}
        return pages.templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "app_name": settings.app_name,
                "title": titles.get(exc.status_code, "Something went wrong"),
                "message": str(exc.detail),
                "detail": None,
            },
            status_code=exc.status_code,
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.path in _DOCS_PATHS:
            response.headers["Content-Security-Policy"] = _DOCS_CSP
        # Local-first, no build step: assets are edited in place. Revalidate
        # instead of letting the browser serve a stale script after an update.
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    return app


app = create_app()
