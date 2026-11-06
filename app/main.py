"""FastAPI application factory (SYSTEM_ARCHITECTURE §1).

Single process serves: Jinja2 pages (+ HTMX partials later) and the JSON API.
Boundary rules live in AGENTS.md §5 — routes stay thin, services hold the logic.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.config import BASE_DIR, settings
from app.logging_setup import setup_logging

logger = logging.getLogger(__name__)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # No inline scripts/styles anywhere in MVP templates (UI spec + CSP, SECURITY §2).
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    ),
}


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
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

    from app.routes import api, pages

    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api/v1")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    return app


app = create_app()
