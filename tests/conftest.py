"""Shared pytest fixtures and helpers (provider fakes arrive in Phase 2 — AGENTS.md §7).

The auth fixtures (`db`, `token`, `no_token`, rate-limit reset) live here
because both `test_auth.py` and `test_authz.py` need them — pytest discovers
conftest fixtures for every test file, no imports required.
"""

import os
import tempfile
from collections.abc import Iterator
from contextlib import suppress

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app import auth as auth_module
from app.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import User
from app.services import users as users_service

#: Known gate token for tests that enable the auth gate (see `token` fixture).
TEST_TOKEN = "test-secret-token"
OWNER_USER = "owner"
OWNER_PASS = "correct-horse-1"


@pytest.fixture(autouse=True)
def _isolate_external_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must never reach a real provider or read a developer key.

    AGENTS.md §7: no live external calls in tests. A developer's `.env` may
    configure AI_PROVIDER=gemini or a Tavily key — tests force the template
    floor, clear every key, disable keyless discovery, and reset the caps so
    behaviour is identical on every machine and in CI.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "ai_provider", "template")
    monkeypatch.setattr(settings, "ai_model", None)
    for attr in (
        "tavily_api_key",
        "serper_api_key",
        "brave_api_key",
        "gemini_api_key",
        "groq_api_key",
        "openrouter_api_key",
        "app_access_token",
    ):
        monkeypatch.setattr(settings, attr, None)
    monkeypatch.setattr(settings, "osm_enabled", False)
    monkeypatch.setattr(settings, "bizdata_enabled", False)
    monkeypatch.setattr(settings, "tavily_daily_cap", 1000)
    monkeypatch.setattr(settings, "serper_daily_cap", 500)
    monkeypatch.setattr(settings, "ai_daily_cap", 200)


def _client_with_csrf(client: TestClient) -> TestClient:
    """Prime a test client with a CSRF cookie by loading the dashboard.

    Subsequent POST calls must include ``csrf_token`` in form data. Use
    ``post_form()`` below to do this automatically.
    """
    client.get("/")
    return client


def post_form(client: TestClient, url: str, data: dict | None = None, **kwargs):
    """POST form data with the CSRF token automatically included."""
    if data is None:
        data = {}
    if "csrf_token" not in data:
        data["csrf_token"] = client.cookies.get("csrf_token", "")
    return client.post(url, data=data, **kwargs)


def post_json(client: TestClient, url: str, data: dict | None = None, **kwargs):
    """POST JSON with the CSRF token automatically included as a header."""
    if data is None:
        data = {}
    headers = kwargs.pop("headers", {})
    headers["X-CSRF-Token"] = client.cookies.get("csrf_token", "")
    return client.post(url, json=data, headers=headers, **kwargs)


def patch_json(client: TestClient, url: str, data: dict | None = None, **kwargs):
    """PATCH JSON with the CSRF token automatically included as a header."""
    if data is None:
        data = {}
    headers = kwargs.pop("headers", {})
    headers["X-CSRF-Token"] = client.cookies.get("csrf_token", "")
    return client.patch(url, json=data, headers=headers, **kwargs)


def _client(**kwargs: object) -> TestClient:
    cookies = kwargs.pop("cookies", None)
    options: dict[str, object] = {"follow_redirects": False}
    options.update(kwargs)
    client = TestClient(app, **options)  # type: ignore[arg-type]
    if cookies:
        # Host-only, like a server-issued cookie: httpx will not match a
        # domain="" jar entry against the server's clear (Max-Age=0).
        for name, value in dict(cookies).items():
            client.cookies.set(name, value, domain="testserver.local")
    return client


def _session_cookie(response: Response) -> str:
    """Extract the session value from a response's Set-Cookie."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{SESSION_COOKIE_NAME}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"no {SESSION_COOKIE_NAME} cookie in {response.headers}")


def _seed_owner(db: Session, *, username: str = OWNER_USER, password: str = OWNER_PASS) -> User:
    """Create the owner account directly (bypasses the /setup round-trip)."""
    if users_service.get_user_by_username(db, username) is None:
        users_service.create_user(db, username=username, password=password, role="owner")
        db.commit()
    return users_service.get_user_by_username(db, username)  # type: ignore[return-value]


def _setup(
    client: TestClient, *, username: str = OWNER_USER, password: str = OWNER_PASS
) -> Response:
    """GET /setup (seeds CSRF) and create the owner account."""
    client.get("/setup")
    return client.post(
        "/setup",
        data={
            "username": username,
            "password": password,
            "confirm": password,
            "csrf_token": client.cookies.get(CSRF_COOKIE_NAME, ""),
            "next": "/",
        },
        follow_redirects=False,
    )


def _login(
    client: TestClient,
    *,
    username: str = OWNER_USER,
    password: str = OWNER_PASS,
    next_url: str | None = "/",
) -> Response:
    """Load the login form (seeds CSRF) and submit credentials."""
    client.get("/login")
    data: dict[str, str] = {"username": username, "password": password}
    if next_url is not None:
        data["next"] = next_url
    return client.post(
        "/login",
        data={**data, "csrf_token": client.cookies.get(CSRF_COOKIE_NAME, "")},
        follow_redirects=False,
    )


@pytest.fixture(autouse=True)
def _clean_rate_limits() -> Iterator[None]:
    """Credential endpoints share process-wide rate buckets — reset per test."""
    auth_module.reset_all_rate_limits()
    yield
    auth_module.reset_all_rate_limits()


@pytest.fixture
def db() -> Iterator[Session]:
    """Fresh temp-file SQLite DB bound to the app's ``get_db`` dependency."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _enable_wal(dbapi_connection: object, _record: object) -> None:
        # Production databases run in WAL; without it a reader (the route's
        # open transaction) blocks the auth middleware's writes.
        dbapi_connection.execute("PRAGMA journal_mode=WAL")  # type: ignore[attr-defined]

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    def _override_get_db() -> Iterator[Session]:
        # Same contract as app.db.get_db: commit at teardown. A route write
        # (e.g. the status card's profile bootstrap) left uncommitted would
        # hold SQLite's write lock while the auth middleware slides the
        # session row -> "database is locked".
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    # AuthMiddleware opens its own session (it runs outside the DI graph):
    # point it at the same test engine so gated requests see test rows.
    factory = sessionmaker(bind=engine)
    monkeypatch_factory = auth_module.SessionLocal
    auth_module.SessionLocal = factory
    try:
        yield session
    finally:
        auth_module.SessionLocal = monkeypatch_factory
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
        for leftover in (path, f"{path}-wal", f"{path}-shm"):
            with suppress(OSError):
                os.unlink(leftover)


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Enable the gate with a known token (restored after the test)."""
    monkeypatch.setattr(settings, "app_access_token", TEST_TOKEN)
    return TEST_TOKEN


@pytest.fixture
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly disable the gate (independent of a local .env)."""
    monkeypatch.setattr(settings, "app_access_token", None)
