"""Phase 5 security tests (SECURITY_AND_PRIVACY.md §9 abuse-case checklist).

All database-touching tests use an isolated temp DB so they can never pollute
the real `data/prospectly.db`.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import suppress

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.main import app
from app.safe_fetcher import SafeFetchError, fetch
from app.safe_fetcher import _validate_url as _validate_url_fn
from app.services.dashboard import dashboard_metrics
from tests.conftest import post_form

# ---------------------------------------------------------------------------
# SafeFetcher SSRF blocking (§9 item 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_fetcher_blocks_file_scheme() -> None:
    """file:// URLs must be rejected (scheme allowlist: http/https only)."""
    with pytest.raises(SafeFetchError, match="Scheme not allowed"):
        await fetch("file:///etc/passwd")


@pytest.mark.asyncio
async def test_safe_fetcher_blocks_private_ip() -> None:
    """Private/loopback IPs must be blocked (SSRF guard)."""
    with pytest.raises(SafeFetchError, match="private/loopback"):
        await fetch("http://127.0.0.1/")
    with pytest.raises(SafeFetchError, match="private/loopback"):
        await fetch("http://localhost/")
    with pytest.raises(SafeFetchError, match="private/loopback"):
        await fetch("http://169.254.169.254/")
    with pytest.raises(SafeFetchError, match="private/loopback"):
        await fetch("http://10.0.0.1/")
    with pytest.raises(SafeFetchError, match="private/loopback"):
        await fetch("http://172.16.0.1/")
    with pytest.raises(SafeFetchError, match="private/loopback"):
        await fetch("http://192.168.1.1/")


@pytest.mark.asyncio
async def test_safe_fetcher_allows_public_url_validation() -> None:
    """Public URL format passes validation (connection may still fail)."""
    result = _validate_url_fn("https://example.com/")
    assert result == "https://example.com/"


# ---------------------------------------------------------------------------
# XSS rendering escaped (§9 item 6)
# ---------------------------------------------------------------------------


def test_xss_renders_escaped_in_templates() -> None:
    """User-supplied content must render escaped, never as executable HTML.

    Uses an isolated temp DB so the test can never pollute the real database.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db: Session = SessionLocal()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        client.get("/")  # prime CSRF cookie

        ts = int(time.time() * 1000)
        # Create a prospect with an XSS payload in the business name.
        unique_name = f'<script>alert("xss-{ts}")</script>'
        r = post_form(
            client,
            "/prospects/new",
            data={
                "business_name": unique_name,
                "phone_raw": f"+44207946{ts % 1000000:06d}",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text
        prospect_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[1])

        # View the detail page — the name must be escaped.
        r = client.get(f"/prospects/{prospect_id}")
        assert r.status_code == 200
        html = r.text
        assert "<script>" not in html
        # The escaped tag should be present as text, not executable HTML.
        assert "&lt;script&gt;" in html
    finally:
        db.close()
        app.dependency_overrides.pop(get_db, None)
        with suppress(OSError):
            os.unlink(path)


# ---------------------------------------------------------------------------
# Log redaction masks API keys (§9 item 7)
# ---------------------------------------------------------------------------


def test_log_redaction_masks_api_keys() -> None:
    """Key-like values in log messages must be redacted."""
    from app.logging_setup import RedactionFilter

    f = RedactionFilter()

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="API_KEY=sk-live-1234567890abcdef was used",
        args=(),
        exc_info=None,
    )
    f.filter(record)
    assert "sk-live-1234567890abcdef" not in record.getMessage()
    assert "***REDACTED***" in record.getMessage()

    record2 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        args=(),
        exc_info=None,
    )
    f.filter(record2)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in record2.getMessage()
    assert "***REDACTED***" in record2.getMessage()


# ---------------------------------------------------------------------------
# Non-loopback bind without token refuses to start (§9 item 8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_loopback_bind_without_token_refuses() -> None:
    """The app must refuse to start on a non-loopback bind without a token."""
    from app.config import settings
    from app.main import lifespan

    original_bind = settings.bind_host
    original_token = settings.app_access_token
    try:
        settings.bind_host = "0.0.0.0"
        settings.app_access_token = None
        cm = lifespan(app)
        with pytest.raises(RuntimeError, match="APP_ACCESS_TOKEN"):
            await cm.__aenter__()
    finally:
        settings.bind_host = original_bind
        settings.app_access_token = original_token


# ---------------------------------------------------------------------------
# Dashboard metrics consistency (§8)
# ---------------------------------------------------------------------------


def test_dashboard_metrics_keys() -> None:
    """dashboard_metrics() returns all §8 metric keys with integer values."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        try:
            metrics = dashboard_metrics(db)
            expected_keys = {
                "total_prospects",
                "researched",
                "requires_review",
                "approved_messages",
                "sent_confirmed",
                "replies",
                "interested",
                "follow_ups_due",
                "converted",
            }
            assert set(metrics.keys()) == expected_keys
            assert all(isinstance(v, int) for v in metrics.values())
        finally:
            db.close()
    finally:
        with suppress(OSError):
            os.unlink(path)


# ---------------------------------------------------------------------------
# CSRF rejection on mutating endpoints (§9 item 3)
# ---------------------------------------------------------------------------


def test_csrf_missing_rejected_on_mutating_endpoints() -> None:
    """Every state-changing endpoint must reject a missing CSRF token."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        # POST without CSRF cookie/token → 403.
        r = client.post("/prospects/new", data={"business_name": "No CSRF Biz"})
        assert r.status_code == 403, f"Expected 403 for missing CSRF cookie, got {r.status_code}"

        # POST with cookie but wrong/missing X-CSRF-Token header (JSON) → 403.
        client.get("/")  # prime CSRF cookie
        r = client.post_json if hasattr(client, "post_json") else client.post
        r = client.post(
            "/api/v1/prospects",
            json={"business_name": "No CSRF API Biz"},
            headers={},
        )
        assert r.status_code == 403, f"Expected 403 for missing CSRF header, got {r.status_code}"

        # PATCH without CSRF token → 403.
        r = client.patch("/api/v1/settings/profile", json={"offer": "x"}, headers={})
        assert r.status_code == 403, f"Expected 403 for missing CSRF on PATCH, got {r.status_code}"
    finally:
        db.close()
        app.dependency_overrides.pop(get_db, None)
        with suppress(OSError):
            os.unlink(path)


# ---------------------------------------------------------------------------
# CSV size and content guards (§9 item 4 / §6)
# ---------------------------------------------------------------------------


def test_csv_import_rejects_oversized_file() -> None:
    """CSV uploads above csv_max_bytes must be rejected (400/413)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        client.get("/")  # prime CSRF cookie

        # Build a body larger than csv_max_bytes (default 2 MB).
        oversized = b"name,city\n" + (b"A" * 3000 + b",Berlin\n") * 4000  # ~12 MB
        r = client.post(
            "/api/v1/imports/preview",
            files={"file": ("big.csv", oversized, "text/csv")},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code in (400, 413), (
            f"Expected 400/413 for oversized CSV, got {r.status_code}"
        )
    finally:
        db.close()
        app.dependency_overrides.pop(get_db, None)
        with suppress(OSError):
            os.unlink(path)


def test_csv_import_rejects_formula_cells() -> None:
    """CSV cells starting with formula chars (= + - @) must be rejected/escaped."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        client.get("/")  # prime CSRF cookie

        csv_with_formula = (
            "name,website\n"
            '=HYPERLINK("http://evil.com"),https://example.com\n'
            "+1234567890,https://example.com\n"
        )
        r = client.post(
            "/api/v1/imports/preview",
            files={"file": ("formula.csv", csv_with_formula.encode(), "text/csv")},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 200, r.text
        preview = r.json()
        # The formula cell row should be marked as an error, not silently accepted.
        assert preview["error_count"] >= 1, f"Expected formula cells to error: {preview}"
    finally:
        db.close()
        app.dependency_overrides.pop(get_db, None)
        with suppress(OSError):
            os.unlink(path)


# ---------------------------------------------------------------------------
# do_not_contact suppression blocks all outbound (§9 item 1)
# ---------------------------------------------------------------------------


def test_do_not_contact_blocks_draft_generate() -> None:
    """A suppressed prospect must return 403 on draft generation."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        from app.services.prospect import set_do_not_contact

        client = TestClient(app)
        client.get("/")  # prime CSRF cookie

        r = client.post(
            "/api/v1/prospects",
            json={"business_name": "Suppressed Biz", "phone_raw": "+442079460958"},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 201, r.text
        prospect_id = r.json()["id"]

        # Advance to outreach_ready so draft gen is allowed by stage.
        from app.services.prospect import transition_stage

        for stage in ("researched", "opportunity_identified", "outreach_ready"):
            transition_stage(db, prospect_id, stage)

        # Suppress.
        set_do_not_contact(db, prospect_id, reason="opt-out", actor="user")

        # Draft generation must be blocked.
        r = client.post(
            f"/api/v1/prospects/{prospect_id}/drafts",
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 403, f"Expected 403 for suppressed draft, got {r.status_code}"
    finally:
        db.close()
        app.dependency_overrides.pop(get_db, None)
        with suppress(OSError):
            os.unlink(path)


def test_do_not_contact_blocks_approve_and_open() -> None:
    """A suppressed prospect must return 403 on approve and open.

    Two scenarios: a pending_review message blocked at approve, and an
    already-approved message blocked at open, each after suppression is set.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        from app.services.outreach import (
            approve_message,
            create_draft,
            submit_message,
        )
        from app.services.prospect import set_do_not_contact, transition_stage

        client = TestClient(app)
        client.get("/")  # prime CSRF cookie

        # --- Prospect A: message in pending_review, then suppressed ---
        r = client.post(
            "/api/v1/prospects",
            json={"business_name": "Suppressed Biz A", "phone_raw": "+442079460959"},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 201, r.text
        prospect_a_id = r.json()["id"]

        for stage in ("researched", "opportunity_identified", "outreach_ready"):
            transition_stage(db, prospect_a_id, stage)

        message_a, _ = create_draft(
            db, prospect_id=prospect_a_id, current_text="Hi A", actor="user"
        )
        submit_message(db, message_id=message_a.id, current_text="Hi A", actor="user")

        # Suppress prospect A.
        set_do_not_contact(db, prospect_a_id, reason="opt-out", actor="user")

        # Approve must be blocked (message is pending_review but prospect suppressed).
        r = client.post(
            f"/api/v1/messages/{message_a.id}/approve",
            json={"ungrounded_acknowledgement": True},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 403, f"Expected 403 for suppressed approve, got {r.status_code}"

        # --- Prospect B: message already approved, then suppressed ---
        r = client.post(
            "/api/v1/prospects",
            json={"business_name": "Suppressed Biz B", "phone_raw": "+442079460958"},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 201, r.text
        prospect_b_id = r.json()["id"]

        for stage in ("researched", "opportunity_identified", "outreach_ready"):
            transition_stage(db, prospect_b_id, stage)

        message_b, _ = create_draft(
            db, prospect_id=prospect_b_id, current_text="Hi B", actor="user"
        )
        submit_message(db, message_id=message_b.id, current_text="Hi B", actor="user")
        approve_message(
            db, message_id=message_b.id, ungrounded_acknowledgement=True, actor="user"
        )  # not suppressed yet

        # Suppress prospect B.
        set_do_not_contact(db, prospect_b_id, reason="opt-out", actor="user")

        # Open must be blocked (message is approved but prospect suppressed).
        r = client.post(
            f"/api/v1/messages/{message_b.id}/open",
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 403, f"Expected 403 for suppressed open, got {r.status_code}"
    finally:
        db.close()
        app.dependency_overrides.pop(get_db, None)
        with suppress(OSError):
            os.unlink(path)
