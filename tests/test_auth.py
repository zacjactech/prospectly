"""Tests for the optional access-token gate (SECURITY_AND_PRIVACY.md §3).

Covers: gate absent when the token is unset, gating when it is set, login
success/failure, CSRF on the login POST, sliding expiry, tampered/expired/
garbage cookies, allowlist, and the open-redirect guard on ``?next=``.

No network: the app runs on a temp-file SQLite DB via a ``get_db`` override
(same pattern as ``tests/test_phase1.py``). ``settings`` is a module singleton,
so the token is set with ``monkeypatch`` and restored automatically.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import suppress

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth import SESSION_COOKIE_NAME, sign_session, verify_session
from app.config import settings
from app.db import Base, get_db
from app.main import app

TEST_TOKEN = "test-secret-token"
OTHER_TOKEN = "other-secret-token"


@pytest.fixture
def db() -> Iterator[Session]:
    """Fresh temp-file SQLite DB bound to the app's ``get_db`` dependency."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    def _override_get_db() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield session
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
        with suppress(OSError):
            os.unlink(path)


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
    options: dict[str, object] = {"follow_redirects": False}
    options.update(kwargs)
    return TestClient(app, **options)  # type: ignore[arg-type]


def _session_cookie(response: Response) -> str:
    """Extract the ``prospectly_session`` value from a response's Set-Cookie."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{SESSION_COOKIE_NAME}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"no {SESSION_COOKIE_NAME} cookie in {response.headers}")


def _login(
    client: TestClient,
    *,
    access_token: str = TEST_TOKEN,
    next_url: str | None = "/",
    with_csrf: bool = True,
) -> Response:
    """Load the login form (seeds CSRF) and submit it."""
    client.get("/login")
    data: dict[str, str] = {"access_token": access_token}
    if with_csrf:
        data["csrf_token"] = client.cookies.get("csrf_token", "")
    if next_url is not None:
        data["next"] = next_url
    return client.post("/login", data=data, follow_redirects=False)


# ===== 1. Token unset ⇒ gate absent =====


def test_gate_absent_when_token_unset(db: Session, no_token: None) -> None:
    client = _client()
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/prospects").status_code == 200
    # /login is a no-op redirect when there is nothing to log in to.
    assert client.get("/login").headers["location"] == "/"


# ===== 2. Token set ⇒ everything gated except the allowlist =====


def test_gate_blocks_pages_api_and_docs(db: Session, token: str) -> None:
    client = _client()

    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=%2F"

    r = client.get("/api/v1/prospects")
    assert r.status_code == 401
    assert set(r.json()) == {"detail"}
    assert "access token" in r.json()["detail"].lower()

    assert client.get("/healthz").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/docs").status_code == 401
    assert client.get("/api/redoc").status_code == 401


def test_allowlist_tolerates_trailing_slash(db: Session, token: str) -> None:
    # /healthz/ must not bounce through the gate into a 404.
    assert _client().get("/healthz/").status_code in {200, 307}


def test_gated_redirect_keeps_query_string(db: Session, token: str) -> None:
    r = _client().get("/prospects?q=acme&stage=new")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=%2Fprospects%3Fq%3Dacme%26stage%3Dnew"


def test_gated_post_is_rejected_not_500(db: Session, token: str) -> None:
    r = _client().post("/api/v1/prospects", json={"business_name": "Acme"})
    assert r.status_code == 401
    r = _client().post("/prospects/new", data={"business_name": "Acme"})
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?next=")


# ===== 3. Wrong token =====


def test_wrong_token_issues_no_session(db: Session, token: str) -> None:
    client = _client()
    r = _login(client, access_token="wrong-token")
    assert r.status_code == 401
    assert "Invalid access token" in r.text
    assert client.cookies.get(SESSION_COOKIE_NAME) is None
    # Still gated afterwards.
    assert client.get("/").status_code == 302
    assert client.get("/api/v1/prospects").status_code == 401

    # Empty submission behaves the same.
    assert _login(client, access_token="").status_code == 401
    # Non-ASCII input must be a clean 401 (compare_digest cannot take non-ASCII str).
    assert _login(client, access_token="p\u00e4ssw\u00f6rd").status_code == 401


# ===== 4. Correct token, next handling, open-redirect guard =====


def test_correct_token_unlocks_pages_and_api(db: Session, token: str) -> None:
    client = _client()
    r = _login(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert verify_session(client.cookies.get(SESSION_COOKIE_NAME)) is not None

    assert client.get("/").status_code == 200
    assert client.get("/api/v1/prospects").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200


def test_next_redirect_honoured_for_local_paths(db: Session, token: str) -> None:
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
        "evil.example",  # relative, not rooted at "/"
        "",
        "/ok\nSet-Cookie: hacked=1",  # control character
    ],
)
def test_next_redirect_rejects_non_local_targets(db: Session, token: str, candidate: str) -> None:
    r = _login(_client(), next_url=candidate)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_oversized_next_is_ignored(db: Session, token: str) -> None:
    r = _login(_client(), next_url="/" + "a" * 4000)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


# ===== 5. Forged / expired / garbage cookies =====


def test_tampered_cookie_is_gated(db: Session, token: str) -> None:
    client = _client()
    _login(client)
    value = client.cookies.get(SESSION_COOKIE_NAME)
    assert value is not None
    tampered = value[:-1] + ("0" if value[-1] != "0" else "1")
    assert tampered != value

    for path, expected in (("/", 302), ("/api/v1/prospects", 401)):
        forged = _client(cookies={SESSION_COOKIE_NAME: tampered})
        assert forged.get(path).status_code == expected


def test_expired_cookie_is_gated(db: Session, token: str) -> None:
    expired = sign_session(int(time.time()) - 5)
    client = _client(cookies={SESSION_COOKIE_NAME: expired})
    assert client.get("/").status_code == 302
    assert client.get("/api/v1/prospects").status_code == 401


def test_session_signed_with_other_token_is_gated(db: Session, token: str, monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_access_token", OTHER_TOKEN)
    foreign = sign_session(int(time.time()) + 3600)
    monkeypatch.setattr(settings, "app_access_token", token)

    client = _client(cookies={SESSION_COOKIE_NAME: foreign})
    assert client.get("/").status_code == 302


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


def test_verify_session_unit(token: str) -> None:
    assert verify_session(None) is None
    assert verify_session("") is None
    assert verify_session("garbage") is None
    # Unicode digits that satisfy str.isdigit() must not reach the MAC encoder.
    assert verify_session("\u00b2\u00b3.abc") is None
    assert verify_session("\u0661\u0662.abc") is None
    # Non-ASCII digest (reachable via a raw latin-1 cookie) must be rejected,
    # not raise from compare_digest.
    future_cookie = sign_session(int(time.time()) + 3600)
    assert verify_session(future_cookie[:-1] + "\u00e9") is None
    # Valid signature but expired: None even though the MAC matches.
    assert verify_session(sign_session(int(time.time()) - 1)) is None
    future = int(time.time()) + 3600
    assert verify_session(sign_session(future)) == future


# ===== 6. Sliding expiry =====


def test_session_expiry_slides_on_authenticated_request(db: Session, token: str) -> None:
    short = int(time.time()) + 60
    client = _client(cookies={SESSION_COOKIE_NAME: sign_session(short)}, follow_redirects=True)

    r = client.get("/")
    assert r.status_code == 200

    refreshed = verify_session(_session_cookie(r))
    assert refreshed is not None
    assert refreshed > short
    # Roughly now + 12h (allow generous scheduling slack).
    assert refreshed >= int(time.time()) + 12 * 60 * 60 - 30


def test_allowlisted_requests_do_not_touch_the_session(db: Session, token: str) -> None:
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert not [h for h in r.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE_NAME)]


# ===== 7. CSRF still applies to the login POST =====


def test_login_post_without_csrf_is_forbidden(db: Session, token: str) -> None:
    client = _client()  # no GET /login ⇒ no csrf cookie
    r = client.post("/login", data={"access_token": TEST_TOKEN}, follow_redirects=False)
    assert r.status_code == 403
    assert r.json()["code"] == "csrf_failure"
    assert client.cookies.get(SESSION_COOKIE_NAME) is None


def test_login_post_with_csrf_succeeds(db: Session, token: str) -> None:
    client = _client()
    r = _login(client)  # includes the csrf_token field
    assert r.status_code == 303
    assert client.cookies.get(SESSION_COOKIE_NAME) is not None


# ===== 8. CSRF token rotation on login =====


def test_login_rotates_csrf_cookie(db: Session, token: str) -> None:
    client = _client()
    pre_login_token = client.get("/login").text  # seeds the cookie
    old_csrf = client.cookies.get("csrf_token")
    assert old_csrf and old_csrf in pre_login_token

    r = client.post(
        "/login",
        data={"access_token": TEST_TOKEN, "csrf_token": old_csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    deletions = [
        h
        for h in r.headers.get_list("set-cookie")
        if h.startswith("csrf_token=") and "Max-Age=0" in h
    ]
    assert deletions, r.headers.get_list("set-cookie")
    assert client.cookies.get("csrf_token") is None

    # The next page load mints a fresh token, different from the pre-login one.
    assert client.get("/").status_code == 200
    new_csrf = client.cookies.get("csrf_token")
    assert new_csrf and new_csrf != old_csrf


# ===== Logout (small extra: the gate has no other exit) =====


def test_logout_clears_session(db: Session, token: str) -> None:
    client = _client()
    _login(client)
    # Login rotated the CSRF cookie; the next page load seeds a fresh one.
    assert client.get("/").status_code == 200
    r = client.post("/logout", data={"csrf_token": client.cookies.get("csrf_token", "")})
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert client.cookies.get(SESSION_COOKIE_NAME) is None
    assert client.get("/").status_code == 302
