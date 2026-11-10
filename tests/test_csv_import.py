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


# ---------------------------------------------------------------------------
# HTML wizard regression (C2): form URLs, multipart CSRF, error handling,
# commit accounting, E.164 phone (M8), session hygiene (M5)
# ---------------------------------------------------------------------------


def _extract(pattern: str, text: str, what: str) -> str:
    import re

    m = re.search(pattern, text)
    assert m, f"could not find {what} in HTML"
    return m.group(1)


def test_html_wizard_form_replay_end_to_end() -> None:
    """Replay exactly what a browser does — read every action and token from
    the rendered HTML instead of hardcoding them. This single flow catches the
    three independent C2 breaks (wrong form URLs, first-visit CSRF token
    mismatch, multipart token extraction) plus commit accounting and the
    E.164 phone guard (M8)."""
    db, path = _make_db()
    try:

        def override_get_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        # Fresh client — NO priming request: the first visit must work (H4).
        client = TestClient(app)

        r = client.get("/settings/import")
        assert r.status_code == 200
        action = _extract(
            r'<form method="post" action="([^"]+)" enctype="multipart/form-data">',
            r.text,
            "upload form action",
        )
        token = _extract(r'name="csrf_token" value="([^"]+)"', r.text, "csrf field")
        # The rendered token MUST equal the cookie the middleware just set.
        assert client.cookies.get("csrf_token") == token, "H4: form/cookie token mismatch"

        csv_content = "name,city,phone\nFirst Visit Biz,Berlin,+49 151 55501234\n"
        r = client.post(
            action,
            data={"csrf_token": token},
            files={"file": ("prospects.csv", csv_content, "text/csv")},
        )
        assert r.status_code == 200, r.text
        assert "Step 2" in r.text
        assert "First Visit Biz" in r.text
        # M8: E.164 '+' phone must be valid, not a formula-injection error.
        assert "invalid characters" not in r.text
        assert "formula character" not in r.text

        commit_action = _extract(
            r'<form method="post" action="([^"]+)">', r.text, "commit form action"
        )
        commit_token = _extract(r'name="csrf_token" value="([^"]+)"', r.text, "csrf field")
        session_token = _extract(r'name="import_token" value="([^"]+)"', r.text, "import_token")
        r = client.post(
            commit_action,
            data={
                "csrf_token": commit_token,
                "import_token": session_token,
                "row_indices": ["0"],
            },
        )
        assert r.status_code == 200, r.text
        assert "Import complete" in r.text
        assert "1 prospect(s) imported" in r.text
    finally:
        _cleanup(db, path)


def test_html_invalid_csv_returns_400_not_500() -> None:
    """A structurally invalid CSV on the page path must render the step-1
    error (400), not crash with a 500 (C2c: wrong ImportError caught)."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        token = client.cookies.get("csrf_token", "")
        r = client.post(
            "/settings/import",
            data={"csrf_token": token},
            files={"file": ("bad.csv", b"city,phone\nBerlin,123\n", "text/csv")},
        )
        assert r.status_code == 400, r.text
        assert "business name" in r.text.lower()
    finally:
        _cleanup(db, path)


def test_api_preview_with_error_row_returns_200() -> None:
    """An error row (missing business name → None) must not 500 the API
    preview flatten step (C2d)."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        csv_content = "name,city\nGood Row,Berlin\n,Bermun\n"
        r = client.post(
            "/api/v1/imports/preview",
            files={"file": ("mixed.csv", csv_content, "text/csv")},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 200, r.text
        preview = r.json()
        assert preview["error_count"] == 1
        error_rows = [row for row in preview["rows"] if row["status"] == "error"]
        assert error_rows and error_rows[0]["business_name"] == ""
        assert "name" in (error_rows[0]["reason"] or "")
    finally:
        _cleanup(db, path)


def test_api_commit_counts_selected_duplicates_and_errors() -> None:
    """Every selected row must appear in the summary: duplicates as
    skipped_duplicates, error rows as errors (C2f: previously all zeros)."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        r = client.post(
            "/api/v1/prospects",
            json={"business_name": "Existing Dup", "phone_raw": "08033333333"},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 201, r.text

        csv_content = "name,city,phone\nExisting Dup,Lagos,08033333333\n,No Name Here,\n"
        r = client.post(
            "/api/v1/imports/preview",
            files={"file": ("dupes.csv", csv_content, "text/csv")},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 200, r.text
        preview = r.json()
        statuses = [row["status"] for row in preview["rows"]]
        assert statuses == ["duplicate", "error"], statuses

        r = client.post(
            "/api/v1/imports/commit",
            json={"import_token": preview["import_token"], "row_indices": [0, 1]},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["created"] == 0
        assert result["skipped_duplicates"] == 1
        assert result["errors"] == 1
    finally:
        _cleanup(db, path)


def test_import_session_token_and_ttl(tmp_path) -> None:
    """M5: tokens are format-checked (no path material reaches the
    filesystem) and sessions expire after 24h; abandoned files are swept."""
    import json
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    from app.config import settings
    from app.services import import_service

    token_hex = "a" * 24
    original_uploads = settings.uploads_dir
    settings.uploads_dir = Path(tmp_path)
    try:
        # Path-style tokens never resolve to a file.
        assert import_service.load_import_session("../../etc/passwd") is None
        import_service.delete_import_session("../../etc/passwd")  # no crash
        with pytest.raises(ValueError):
            import_service.save_import_session("../evil", {})

        # Fresh session loads; expired one is deleted and refused.
        import_service.save_import_session(token_hex, {"rows": []})
        assert import_service.load_import_session(token_hex) == {"rows": []}

        session_file = import_service._session_path(token_hex)
        assert session_file is not None and session_file.exists()
        payload = json.loads(session_file.read_text(encoding="utf-8"))
        payload["created_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        session_file.write_text(json.dumps(payload), encoding="utf-8")
        assert import_service.load_import_session(token_hex) is None
        assert not session_file.exists()

        # Abandoned stale file is swept on the next save.
        stale = import_service._session_path("b" * 24)
        assert stale is not None
        stale.write_text("{}", encoding="utf-8")
        old = (datetime.now(UTC) - timedelta(hours=25)).timestamp()
        os.utime(stale, (old, old))
        import_service.save_import_session("c" * 24, {"rows": []})
        assert not stale.exists()
        import_service.delete_import_session("c" * 24)
    finally:
        settings.uploads_dir = original_uploads
