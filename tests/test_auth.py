"""Tests for the account auth system (SECURITY_AND_PRIVACY §3).

Full accounts + roles (owner decision 2026-11-18): the access token is only
the gate switch; credentials live in ``users`` (scrypt) and sessions are
revocable rows in ``user_sessions``.

Covers: gate absent when the token is unset, gating when it is set, first-run
setup, login success/failure/rate-limit/disabled, CSRF + rotation, session
revocation (logout / logout-all / disable / password reset), sliding expiry,
tampered/expired/garbage cookies, allowlist, the open-redirect guard on
``?next=``, signup env-gating, the forgot/reset flow, and the users/authz
services.

No network: the app runs on a temp-file SQLite DB via a ``get_db`` override.
``settings`` is a module singleton, so flags are set with ``monkeypatch`` and
restored automatically. Rate-limit buckets are cleared around every test.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from collections.abc import Iterator
from contextlib import suppress

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from app import auth as auth_module
from app.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    create_session,
    lookup_session,
)
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Prospect, User, UserSession
from app.services import authz
from app.services import users as users_service

TEST_TOKEN = "test-secret-token"
OWNER_USER = "owner"
OWNER_PASS = "correct-horse-1"


@pytest.fixture(autouse=True)
def _clean_rate_limits() -> Iterator[None]:
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


# ===== 1. Token unset => gate absent =====


def test_gate_absent_when_token_unset(db: Session, no_token: None) -> None:
    client = _client()
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/prospects").status_code == 200
    # Auth pages are no-ops when there is nothing to gate.
    assert client.get("/login").headers["location"] == "/"
    assert client.get("/setup").headers["location"] == "/"
    # Permissions pass through too (loopback MVP has no identity).
    assert client.get("/settings/general").status_code == 200


# ===== 2. Gate set, no accounts yet => everyone funnels to /setup =====


def test_first_run_funnels_to_setup(db: Session, token: str) -> None:
    client = _client()
    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["location"] == "/setup"

    assert client.get("/prospects?q=acme").headers["location"] == "/setup"
    # API/JSON never receives an HTML page.
    api = client.get("/api/v1/prospects")
    assert api.status_code == 401
    assert "/setup" in api.json()["detail"]

    assert client.get("/healthz").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/api/openapi.json").status_code == 401

    # The setup form itself is reachable and creates the owner.
    assert client.get("/setup").status_code == 200
    assert "Create the owner account" in client.get("/setup").text


def test_setup_form_validations(db: Session, token: str) -> None:
    client = _client()
    client.get("/setup")
    csrf = client.cookies.get(CSRF_COOKIE_NAME, "")

    # Mismatched confirmation.
    r = client.post(
        "/setup",
        data={
            "username": "owner",
            "password": "correct-horse-1",
            "confirm": "nope",
            "csrf_token": csrf,
        },
    )
    assert r.status_code == 200
    assert "do not match" in r.text

    # Weak password.
    r = client.post(
        "/setup",
        data={"username": "owner", "password": "short", "confirm": "short", "csrf_token": csrf},
    )
    assert r.status_code == 200
    assert "at least 8" in r.text

    # Bad username.
    r = client.post(
        "/setup",
        data={
            "username": "A B",
            "password": "correct-horse-1",
            "confirm": "correct-horse-1",
            "csrf_token": csrf,
        },
    )
    assert r.status_code == 200
    assert "Username must be" in r.text

    # Missing CSRF is rejected before the handler runs.
    r = client.post(
        "/setup",
        data={"username": "owner", "password": "correct-horse-1", "confirm": "correct-horse-1"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "csrf_failure"


def test_setup_creates_owner_and_signs_in(db: Session, token: str) -> None:
    client = _client()
    r = _setup(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert client.cookies.get(SESSION_COOKIE_NAME)

    owner = db.scalar(select(User).where(User.username == OWNER_USER))
    assert owner is not None and owner.role == "owner"
    assert users_service.verify_password(OWNER_PASS, owner.password_hash)

    # Signed in: the app opens.
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/prospects").status_code == 200

    # /setup and /login bounce away once an account exists.
    assert client.get("/setup").headers["location"] == "/login"
    assert client.get("/login").headers["location"] == "/"


# ===== 3. Login =====


def test_login_success_issues_session(db: Session, token: str) -> None:
    _seed_owner(db)
    client = _client()
    r = _login(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    session_token = _session_cookie(r)
    assert lookup_session(db, session_token) is not None

    assert client.get("/").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200


def test_wrong_password_and_unknown_user_share_one_message(db: Session, token: str) -> None:
    _seed_owner(db)
    client = _client()

    r = _login(client, password="wrong-password")
    assert r.status_code == 401
    wrong_msg = "Invalid username or password."
    assert wrong_msg in r.text
    assert client.cookies.get(SESSION_COOKIE_NAME) is None

    # Unknown user: identical status + copy (no user enumeration).
    r = _login(client, username="ghost", password="whatever-1")
    assert r.status_code == 401
    assert wrong_msg in r.text

    # Empty submission behaves the same.
    r = _login(client, username="", password="")
    assert r.status_code == 401


def test_login_is_rate_limited(db: Session, token: str) -> None:
    _seed_owner(db)
    client = _client()
    for _ in range(5):
        assert _login(client, password="wrong-password").status_code == 401
    r = _login(client, password="wrong-password")
    assert r.status_code == 429
    assert "Too many attempts" in r.text
    # Even the correct password waits out the window.
    assert _login(client).status_code == 429


def test_disabled_user_cannot_login_and_existing_sessions_die(db: Session, token: str) -> None:
    owner = _seed_owner(db)
    # A live session from before the disable…
    session = create_session(db, owner.id)
    session_token = session.token  # the middleware deletes this row below;
    db.commit()  # re-reading the instance would refresh it
    client = _client(cookies={SESSION_COOKIE_NAME: session_token})
    assert client.get("/").status_code == 200

    # …then the owner is disabled.
    owner.disabled = True
    db.commit()

    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")
    assert "reason=disabled" in r.headers["location"]
    assert client.cookies.get(SESSION_COOKIE_NAME) is None
    assert lookup_session(db, session_token) is None  # revoked server-side

    # And login refuses up front with its own copy.
    client.cookies.delete(SESSION_COOKIE_NAME)
    r = _login(client)
    assert r.status_code == 403
    assert "disabled" in r.text.lower()


# ===== 4. next handling + open-redirect guard (unchanged contract) =====


def test_next_redirect_honoured_for_local_paths(db: Session, token: str) -> None:
    _seed_owner(db)
    r = _login(_client(), next_url="/prospects?q=acme")
    assert r.status_code == 303
    assert r.headers["location"] == "/prospects?q=acme"


@pytest.mark.parametrize(
    "candidate",
    [
        "https://evil.example/steal",
        "http://evil.example",
        "//evil.example/path",
        "/\\evil.example",
        "javascript:alert(1)",
        "evil.example",
        "",
        "/ok\nSet-Cookie: hacked=1",
    ],
)
def test_next_redirect_rejects_non_local_targets(db: Session, token: str, candidate: str) -> None:
    _seed_owner(db)
    r = _login(_client(), next_url=candidate)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_oversized_next_is_ignored(db: Session, token: str) -> None:
    _seed_owner(db)
    r = _login(_client(), next_url="/" + "a" * 4000)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


# ===== 5. Forged / expired / garbage cookies =====


def test_tampered_cookie_is_gated(db: Session, token: str) -> None:
    _seed_owner(db)
    client = _client()
    _login(client)
    value = client.cookies.get(SESSION_COOKIE_NAME)
    assert value is not None
    tampered = value[:-1] + ("0" if value[-1] != "0" else "1")
    assert tampered != value

    for path, expected in (("/", 302), ("/api/v1/prospects", 401)):
        forged = _client(cookies={SESSION_COOKIE_NAME: tampered})
        assert forged.get(path).status_code == expected


def test_expired_session_row_is_purged_and_gated(db: Session, token: str) -> None:
    owner = _seed_owner(db)
    session = create_session(db, owner.id)
    session.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=5)
    db.commit()

    session_id = session.id
    client = _client(cookies={SESSION_COOKIE_NAME: session.token})
    assert client.get("/").status_code == 302
    # lookup purged the dead row (a query, not db.get: the identity map
    # would hand back the stale instance without touching the database).
    assert db.scalar(select(UserSession).where(UserSession.id == session_id)) is None


@pytest.mark.parametrize(
    "garbage",
    ["garbage", "", ".", "..", "123.", ".deadbeef", "abc.def", "1.2.3", "x" * 400],
)
def test_garbage_cookie_never_500(db: Session, token: str, garbage: str) -> None:
    client = _client(cookies={SESSION_COOKIE_NAME: garbage})
    assert client.get("/").status_code == 302
    r = client.get("/api/v1/prospects")
    assert r.status_code == 401
    assert "detail" in r.json()
    # Non-ascii never reaches the wire (httpx refuses it) — guard the layer
    # that would see it from a raw header instead.
    assert lookup_session(db, "caf\u00e9-caf\u00e9") is None


def test_lookup_session_unit(db: Session, token: str) -> None:
    owner = _seed_owner(db)
    session = create_session(db, owner.id)
    db.commit()
    assert lookup_session(db, session.token) is not None
    assert lookup_session(db, None) is None
    assert lookup_session(db, "garbage") is None
    assert lookup_session(db, "x" * 500) is None


# ===== 6. Sliding expiry (row touch) =====


def test_session_expiry_slides_on_authenticated_request(db: Session, token: str) -> None:
    owner = _seed_owner(db)
    session = create_session(db, owner.id)
    session.expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60)
    short = session.expires_at  # read BEFORE commit (expire_on_commit would
    db.commit()  # lazily re-SELECT and hold a read txn that
    # blocks the middleware's sliding UPDATE)
    client = _client(cookies={SESSION_COOKIE_NAME: session.token})
    r = client.get("/")
    assert r.status_code == 200

    db.refresh(session)
    extended = session.expires_at
    if extended.tzinfo is None:
        extended = extended.replace(tzinfo=dt.UTC)
    assert extended > short
    assert extended >= dt.datetime.now(dt.UTC) + dt.timedelta(seconds=11 * 60 * 60)


def test_allowlisted_requests_do_not_touch_the_session(db: Session, token: str) -> None:
    owner = _seed_owner(db)
    session = create_session(db, owner.id)
    db.commit()
    client = _client(cookies={SESSION_COOKIE_NAME: session.token})

    r = client.get("/healthz")
    assert r.status_code == 200
    assert not [h for h in r.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE_NAME)]


# ===== 7. CSRF on the credential POSTs + rotation on login =====


def test_login_post_without_csrf_is_forbidden(db: Session, token: str) -> None:
    _seed_owner(db)
    client = _client()  # no GET /login ⇒ no csrf cookie
    r = client.post("/login", data={"username": OWNER_USER, "password": OWNER_PASS})
    assert r.status_code == 403
    assert r.json()["code"] == "csrf_failure"
    assert client.cookies.get(SESSION_COOKIE_NAME) is None


def test_login_rotates_csrf_cookie(db: Session, token: str) -> None:
    _seed_owner(db)
    client = _client()
    pre_login_token = client.get("/login").text  # seeds the cookie
    old_csrf = client.cookies.get(CSRF_COOKIE_NAME)
    assert old_csrf and old_csrf in pre_login_token

    r = _login(client)
    assert r.status_code == 303
    deletions = [
        h
        for h in r.headers.get_list("set-cookie")
        if h.startswith("csrf_token=") and "Max-Age=0" in h
    ]
    assert deletions, r.headers.get_list("set-cookie")
    assert client.cookies.get(CSRF_COOKIE_NAME) is None

    # The next page load mints a fresh token, different from the pre-login one.
    assert client.get("/").status_code == 200
    new_csrf = client.cookies.get(CSRF_COOKIE_NAME)
    assert new_csrf and new_csrf != old_csrf


# ===== 8. Logout / logout-all (server-side revocation) =====


def test_logout_clears_session_and_revokes_row(db: Session, token: str) -> None:
    _seed_owner(db)
    client = _client()
    _login(client)
    value = client.cookies.get(SESSION_COOKIE_NAME)
    assert lookup_session(db, value) is not None

    # Login rotated the CSRF cookie; the next page load seeds a fresh one.
    assert client.get("/").status_code == 200
    r = client.post("/logout", data={"csrf_token": client.cookies.get(CSRF_COOKIE_NAME, "")})
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert client.cookies.get(SESSION_COOKIE_NAME) is None
    assert lookup_session(db, value) is None  # revoked, not just forgotten
    assert client.get("/").status_code == 302


def test_logout_all_kills_every_session(db: Session, token: str) -> None:
    owner = _seed_owner(db)
    first = create_session(db, owner.id)
    second = create_session(db, owner.id)
    first_token, second_token = first.token, second.token  # rows die below
    db.commit()

    client = _client(cookies={SESSION_COOKIE_NAME: first_token})
    assert client.get("/").status_code == 200
    r = client.post("/logout-all", data={"csrf_token": client.cookies.get(CSRF_COOKIE_NAME, "")})
    assert r.status_code == 303
    assert lookup_session(db, first_token) is None
    assert lookup_session(db, second_token) is None
    # The other device is gated now.
    other = _client(cookies={SESSION_COOKIE_NAME: second_token})
    assert other.get("/").status_code == 302


# ===== 9. Signup (env-gated) =====


def test_signup_hidden_by_default(db: Session, token: str) -> None:
    _seed_owner(db)
    client = _client()
    assert client.get("/signup").headers["location"] == "/login"
    r = client.post(
        "/signup",
        data={
            "username": "newbie",
            "password": "password1",
            "confirm": "password1",
            "csrf_token": client.cookies.get(CSRF_COOKIE_NAME, ""),
        },
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert users_service.get_user_by_username(db, "newbie") is None


def test_signup_creates_member_when_enabled(db: Session, token: str, monkeypatch) -> None:
    monkeypatch.setattr(settings, "allow_signup", True)
    _seed_owner(db)
    client = _client()
    assert client.get("/signup").status_code == 200

    client.get("/signup")  # seeds CSRF
    r = client.post(
        "/signup",
        data={
            "username": "Newbie",
            "password": "password1",
            "confirm": "password1",
            "csrf_token": client.cookies.get(CSRF_COOKIE_NAME, ""),
        },
    )
    assert r.status_code == 303
    member = users_service.get_user_by_username(db, "newbie")  # normalized
    assert member is not None and member.role == "member"
    assert client.cookies.get(SESSION_COOKIE_NAME)  # signed in
    # …and members are not owners.
    assert not authz.has_permission(member.role, "users")


# ===== 10. Forgot / reset password =====


def test_forgot_password_shows_link_only_for_existing_user(db: Session, token: str) -> None:
    _seed_owner(db)
    client = _client()

    r = client.post(
        "/forgot-password",
        data={"username": "ghost", "csrf_token": _csrf(client, "/forgot-password")},
    )
    assert r.status_code == 200
    assert "/reset-password?token=" not in r.text

    r = client.post(
        "/forgot-password",
        data={"username": OWNER_USER, "csrf_token": _csrf(client, "/forgot-password")},
    )
    assert r.status_code == 200
    assert "/reset-password?token=" in r.text
    owner = users_service.get_user_by_username(db, OWNER_USER)
    assert owner is not None and owner.reset_token_hash is not None


def test_reset_password_flow_revokes_sessions(db: Session, token: str) -> None:
    owner = _seed_owner(db)
    client = _client()
    session = create_session(db, owner.id)
    session_token = session.token  # reset revokes it via the same test session
    db.commit()

    token_plain = users_service.issue_reset_token(db, owner)
    db.commit()

    # Confirmation mismatch keeps the form.
    r = client.post(
        "/reset-password",
        data={
            "token": token_plain,
            "password": "brand-new-pass",
            "confirm": "nope",
            "csrf_token": _csrf(client, "/reset-password?token=x"),
        },
    )
    assert r.status_code == 200
    assert "do not match" in r.text

    # Weak password is refused (policy) and the token stays valid.
    r = client.post(
        "/reset-password",
        data={
            "token": token_plain,
            "password": "short",
            "confirm": "short",
            "csrf_token": _csrf(client, "/reset-password?token=x"),
        },
    )
    assert r.status_code == 200
    assert "at least 8" in r.text

    # Valid reset: password changes, every session dies, token consumed.
    r = client.post(
        "/reset-password",
        data={
            "token": token_plain,
            "password": "brand-new-pass",
            "confirm": "brand-new-pass",
            "csrf_token": _csrf(client, "/reset-password?token=x"),
        },
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login?reason=reset"
    db.refresh(owner)
    assert users_service.verify_password("brand-new-pass", owner.password_hash)
    assert owner.reset_token_hash is None
    assert lookup_session(db, session_token) is None

    # Single use: replaying the token fails.
    r = client.post(
        "/reset-password",
        data={
            "token": token_plain,
            "password": "brand-new-pass",
            "confirm": "brand-new-pass",
            "csrf_token": _csrf(client, "/reset-password?token=x"),
        },
    )
    assert r.status_code == 400
    assert "invalid or has expired" in r.text


def test_reset_token_expires(db: Session, token: str) -> None:
    owner = _seed_owner(db)
    token_plain = users_service.issue_reset_token(db, owner)
    owner.reset_expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    db.commit()

    client = _client()
    r = client.post(
        "/reset-password",
        data={
            "token": token_plain,
            "password": "brand-new-pass",
            "confirm": "brand-new-pass",
            "csrf_token": _csrf(client, "/reset-password?token=x"),
        },
    )
    assert r.status_code == 400
    assert "invalid or has expired" in r.text


def _csrf(client: TestClient, path: str) -> str:
    """Seed the CSRF cookie by GETting ``path`` and return its value."""
    client.get(path)
    return client.cookies.get(CSRF_COOKIE_NAME, "")


# ===== 11. Services: hashing, policy, reset tokens =====


def test_scrypt_roundtrip_and_garbage(db: Session) -> None:
    stored = users_service.hash_password("correct-horse-1")
    assert stored.startswith("scrypt$16384$8$1$")
    assert users_service.verify_password("correct-horse-1", stored)
    assert not users_service.verify_password("correct-horse-2", stored)
    # Corrupt store strings fail closed instead of raising.
    assert not users_service.verify_password("x", "garbage")
    assert not users_service.verify_password("x", "scrypt$bad$format")
    assert not users_service.verify_password("x", "bcrypt$1$2$3$4$5")


def test_username_and_password_policy() -> None:
    assert users_service.validate_username("  Ada_Lovelace99 ") == "ada_lovelace99"
    for bad in ("", "ab", "A B", "x" * 41, "emoji😀"):
        with pytest.raises(ValueError):
            users_service.validate_username(bad)

    assert users_service.validate_password("correct-horse-1", username="owner")
    with pytest.raises(ValueError, match="at least 8"):
        users_service.validate_password("short", username="owner")
    with pytest.raises(ValueError, match="username"):
        users_service.validate_password("owner-name", username="owner-name")
    with pytest.raises(ValueError):
        users_service.validate_password("        ", username="owner")


def test_create_user_rejects_duplicates(db: Session) -> None:
    users_service.create_user(db, username="duplicated", password="correct-horse-1")
    db.commit()
    with pytest.raises(ValueError, match="taken"):
        users_service.create_user(db, username="DUPLICATED", password="correct-horse-1")


# ===== 12. Authorization policy =====


def test_permission_matrix() -> None:
    assert authz.has_permission("owner", "settings")
    assert authz.has_permission("owner", "danger")
    assert authz.has_permission("owner", "users")
    assert authz.has_permission("admin", "settings")
    assert authz.has_permission("admin", "danger")
    assert not authz.has_permission("admin", "users")
    assert not authz.has_permission("member", "settings")
    assert not authz.has_permission("member", "danger")
    assert not authz.has_permission("member", "users")
    # Fail closed: unknown role or permission.
    assert not authz.has_permission(None, "settings")
    assert not authz.has_permission("superuser", "settings")
    assert not authz.has_permission("owner", "unknown-permission")
    assert authz.PERMISSION_MESSAGES["users"].startswith("Only the owner")


def test_prospect_visibility_and_scoping(db: Session) -> None:
    shared = Prospect(business_name="Shared Ltd")
    mine = Prospect(business_name="Mine Ltd", owner_id=7)
    theirs = Prospect(business_name="Theirs Ltd", owner_id=8)
    db.add_all([shared, mine, theirs])
    db.commit()

    member = User(id=7, username="member1", password_hash="x", role="member")
    other = User(id=8, username="member2", password_hash="x", role="member")
    owner = User(id=9, username="boss", password_hash="x", role="owner")
    admin = User(id=10, username="admin1", password_hash="x", role="admin")
    db.add_all([member, other, owner, admin])
    db.commit()

    # Gate off / no identity: everything visible.
    assert authz.can_see_prospect(None, 7) is True
    # Member: shared + own, never someone else's.
    assert authz.can_see_prospect(member, None) is True
    assert authz.can_see_prospect(member, 7) is True
    assert authz.can_see_prospect(member, 8) is False
    # Owner/admin: everything.
    assert authz.can_see_prospect(owner, 8) is True
    assert authz.can_see_prospect(admin, 8) is True

    # Scoping helper: member sees shared+own ids; admin gets no restriction.
    member_ids = authz.scoped_prospect_ids(db, member)
    assert set(member_ids) == {shared.id, mine.id}
    assert authz.scoped_prospect_ids(db, owner) is None
    assert authz.scoped_prospect_ids(db, None) is None

    # visible_prospect enforces the same rule on a fetch.
    assert authz.visible_prospect(db, member, theirs.id) is None
    assert authz.visible_prospect(db, member, mine.id) is mine
    assert authz.visible_prospect(db, member, 999999) is None


# ===== 13. require_permission dependency =====


def _request_with_role(role: str | None) -> Request:
    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    if role is not None:
        request.state.user_id = 1
        request.state.user_username = "u"
        request.state.user_role = role
    return request


def test_require_permission_dependency(token: str, monkeypatch) -> None:
    dep = auth_module.require_permission("settings")

    # Member is denied with the policy copy.
    with pytest.raises(HTTPException) as exc:
        dep(_request_with_role("member"))
    assert exc.value.status_code == 403
    assert "admin or owner" in str(exc.value.detail)

    # Admin passes; owner passes; users is owner-only.
    assert dep(_request_with_role("admin")) is not None
    with pytest.raises(HTTPException):
        auth_module.require_permission("users")(_request_with_role("admin"))
    assert auth_module.require_permission("users")(_request_with_role("owner")) is not None

    # Gate off: pass-through (no identity, no denial).
    monkeypatch.setattr(settings, "app_access_token", None)
    assert dep(_request_with_role(None)) is None
