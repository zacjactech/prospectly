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
