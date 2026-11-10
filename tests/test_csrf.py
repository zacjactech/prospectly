"""CSRF regression tests (SECURITY_AND_PRIVACY §3, review findings H4 + C2b).

H4: on a first visit the rendered form token must equal the cookie the
middleware sets — previously the form got one random token and the cookie
another, so every new session's first state-changing submit failed 403.

C2b: multipart/form-data uploads must accept the token from the form body
(plain HTML forms cannot send the X-CSRF-Token header).
"""

from __future__ import annotations

import os
import re
import tempfile
from contextlib import suppress

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.main import app


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    return db, path


def _cleanup(db: Session, path: str) -> None:
    db.close()
    app.dependency_overrides.clear()
    with suppress(OSError):
        os.unlink(path)


def _form_token(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "rendered form has no csrf_token field"
    return m.group(1)


def test_first_visit_form_token_matches_cookie_and_submit_succeeds() -> None:
    """H4 regression: a brand-new browser session's first submit must not 403."""
    db, path = _make_db()
    try:

        def override_get_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)  # no priming request — truly first visit

        r = client.get("/prospects/new")
        assert r.status_code == 200
        rendered = _form_token(r.text)
        cookie = client.cookies.get("csrf_token")
        assert cookie is not None, "middleware did not set a cookie"
        assert rendered == cookie, "H4: rendered form token != cookie token"

        r = client.post(
            "/prospects/new",
            data={"csrf_token": rendered, "business_name": "First Visit Co"},
            follow_redirects=False,
        )
        assert r.status_code != 403, f"first submit rejected: {r.text}"
        assert r.status_code in (200, 303), r.text

        # The prospect actually got created (the submit did real work).
        r = client.get("/api/v1/prospects?q=First Visit")
        assert r.status_code == 200
        assert any(p["business_name"] == "First Visit Co" for p in r.json())
    finally:
        _cleanup(db, path)


def test_second_visit_uses_cookie_token_stable() -> None:
    """Subsequent renders keep using the existing cookie token (no rotation
    that would strand an open form)."""
    db, path = _make_db()
    try:

        def override_get_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        first = client.get("/prospects/new")
        token1 = _form_token(first.text)
        second = client.get("/prospects/new")
        token2 = _form_token(second.text)
        assert token1 == token2 == client.cookies.get("csrf_token")
    finally:
        _cleanup(db, path)


def test_multipart_form_token_accepted() -> None:
    """C2b regression: a multipart upload carrying the token in the form body
    passes CSRF (previously only urlencoded bodies were parsed → 403)."""
    db, path = _make_db()
    try:

        def override_get_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        r = client.get("/settings/import")  # primes cookie + renders form
        assert r.status_code == 200
        token = _form_token(r.text)

        csv_content = "name,city\nMultipart Biz,Berlin\n"
        r = client.post(
            "/settings/import",
            data={"csrf_token": token},
            files={"file": ("m.csv", csv_content, "text/csv")},
        )
        assert r.status_code != 403, f"multipart rejected by CSRF: {r.text}"
        assert r.status_code == 200, r.text
        assert "Step 2" in r.text
    finally:
        _cleanup(db, path)


def test_multipart_wrong_token_rejected() -> None:
    """The multipart path must still fail closed on a wrong token."""
    db, path = _make_db()
    try:

        def override_get_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        client.get("/settings/import")  # primes cookie

        csv_content = "name,city\nX,Berlin\n"
        r = client.post(
            "/settings/import",
            data={"csrf_token": "deadbeef" * 8},
            files={"file": ("m.csv", csv_content, "text/csv")},
        )
        assert r.status_code == 403
        assert r.json()["code"] == "csrf_failure"
    finally:
        _cleanup(db, path)


def test_multipart_missing_token_rejected() -> None:
    """No token at all in a multipart body → 403 (fail closed)."""
    db, path = _make_db()
    try:

        def override_get_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        client.get("/settings/import")

        csv_content = "name,city\nX,Berlin\n"
        r = client.post(
            "/settings/import",
            files={"file": ("m.csv", csv_content, "text/csv")},
        )
        assert r.status_code == 403
        assert r.json()["code"] == "csrf_failure"
    finally:
        _cleanup(db, path)


def test_non_ascii_token_fails_closed_without_exception() -> None:
    """compare_digest raises TypeError on non-ASCII str input — a raw client
    could have turned any mutating request into a 500. The comparison now
    encodes to bytes and fails closed (403 semantics). Unit-level because
    httpx refuses to put non-ASCII bytes on the wire (that's precisely why
    the bug was only reachable by raw sockets)."""
    from app.csrf import _tokens_match

    assert _tokens_match("café", "tëst") is False
    assert _tokens_match("ascii-ok", "tëst") is False
    assert _tokens_match("tëst", "tëst") is True  # equal non-ASCII still compares
    assert _tokens_match(None, "x") is False
