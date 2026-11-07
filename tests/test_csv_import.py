"""CSV import tests (Flow 10, PRODUCT_BRIEF §6).

Tests the import service + API endpoints: parsing, dedupe detection, commit.
Uses the temp-file SQLite pattern from test_phase1.py.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.main import app
from app.services.import_service import ImportError as ImportServiceError
from app.services.import_service import parse_csv


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    return db, path


def _client_with_db(db: Session) -> TestClient:
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    client.get("/")  # prime CSRF cookie
    return client


def _cleanup(db: Session, path: str) -> None:
    db.close()
    app.dependency_overrides.clear()
    with suppress(OSError):
        os.unlink(path)


def _csv(content: str) -> bytes:
    return content.encode("utf-8")


def test_parse_valid_csv() -> None:
    """A well-formed CSV parses into valid rows with correct fields."""
    result = parse_csv(
        _csv(
            "name,city,phone,email,website\nAcme Hotel,Lagos,08012345678,info@acme.example,https://acme.example\n"
        ),
        default_country=None,
    )
    assert result["total_rows"] == 1
    assert result["valid_count"] == 1
    assert result["error_count"] == 0
    row = result["rows"][0]
    assert row["fields"]["business_name"] == "Acme Hotel"
    assert row["fields"]["location_city"] == "Lagos"
    assert row["status"] == "valid"


def test_parse_missing_business_name_column() -> None:
    """CSV without a business name column raises ImportError."""
    with pytest.raises(ImportServiceError) as exc:
        parse_csv(_csv("city,phone\nLagos,08012345678\n"), default_country=None)
    assert "business name" in str(exc.value).lower()


def test_parse_empty_csv() -> None:
    """Empty CSV raises ImportError."""
    with pytest.raises(ImportServiceError):
        parse_csv(b"", default_country=None)


def test_parse_invalid_email_and_url() -> None:
    """Rows with invalid email or URL are marked as errors."""
    result = parse_csv(
        _csv("name,email,website\nBad Biz,not-an-email,ftp://bad.example\n"),
        default_country=None,
    )
    assert result["error_count"] == 1
    assert result["rows"][0]["status"] == "error"
    reason = (result["rows"][0]["reason"] or "").lower()
    assert "email" in reason or "website" in reason


def test_api_import_preview_and_commit() -> None:
    """End-to-end: preview CSV via API, then commit selected rows."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)

        # First create an existing prospect to test duplicate detection.
        r = client.post(
            "/api/v1/prospects",
            json={
                "business_name": "Existing Biz",
                "phone_raw": "08011111111",
                "email": "info@example.com",
            },
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 201, r.text

        # Upload CSV with one duplicate + one new prospect.
        csv_content = (
            "name,city,phone,email,website\n"
            "Existing Biz,Lagos,08011111111,info@example.com,https://existing.example\n"
            "New Prospect,Abuja,08022222222,info@new.example,https://new.example\n"
        )
        r = client.post(
            "/api/v1/imports/preview",
            files={"file": ("prospects.csv", csv_content, "text/csv")},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 200, r.text
        preview = r.json()
        assert preview["total_rows"] == 2
        assert preview["duplicate_count"] == 1
        assert preview["valid_count"] == 1

        # Commit only the valid row (index 1) using the real token from preview.
        r = client.post(
            "/api/v1/imports/commit",
            json={"import_token": preview["import_token"], "row_indices": [1]},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        result = r.json()
        assert result["created"] == 1
        assert result["skipped_duplicates"] == 0

        # Verify the new prospect exists.
        r = client.get("/api/v1/prospects?q=New")
        assert r.status_code == 200
        prospects = r.json()
        assert any(p["business_name"] == "New Prospect" for p in prospects)

        # Re-commit with the same (expired) token → 404.
        r = client.post(
            "/api/v1/imports/commit",
            json={"import_token": preview["import_token"], "row_indices": [1]},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 404, f"Expected 404 for expired token, got {r.status_code}"

    finally:
        _cleanup(db, path)


def test_import_page_renders() -> None:
    """The import upload page renders."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        r = client.get("/settings/import")
        assert r.status_code == 200
        assert "Import prospects from CSV" in r.text
    finally:
        _cleanup(db, path)
