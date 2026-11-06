"""Phase 1 tests: prospect CRUD, dedupe, stage transitions, profile.

Tests use TestClient against the in-memory app with a fresh SQLite DB per test
(function-scoped override of get_db to a temp file).
"""

from __future__ import annotations

import os
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


def _client_with_db(db: Session) -> TestClient:
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _cleanup(db: Session, path: str) -> None:
    db.close()
    app.dependency_overrides.clear()
    with suppress(OSError):
        os.unlink(path)


def test_prospect_crud_lifecycle() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)

        # Create
        r = client.post(
            "/prospects/new",
            data={
                "business_name": "Acme Hotel",
                "category": "hotel",
                "location_city": "Ikorodu",
                "phone_raw": "08012345678",
                "email": "info@acmehotel.example",
                "website_url": "https://acmehotel.example",
            },
            follow_redirects=False,
        )
        assert r.status_code in (200, 303), r.text
        prospect_id = r.headers["location"].split("/")[-1] if r.status_code == 303 else None

        # Fallback: find by name via API
        if prospect_id is None:
            r = client.get("/api/v1/prospects?q=Acme")
            assert r.status_code == 200
            prospects = r.json()
            assert len(prospects) == 1
            prospect_id = str(prospects[0]["id"])

        # Read
        r = client.get(f"/prospects/{prospect_id}")
        assert r.status_code == 200
        assert "Acme Hotel" in r.text

        # Update
        r = client.post(
            f"/prospects/{prospect_id}/edit",
            data={
                "business_name": "Acme Hotel & Suites",
                "category": "hotel",
                "location_city": "Ikorodu",
                "phone_raw": "08012345678",
                "email": "info@acmehotel.example",
                "website_url": "https://acmehotel.example",
            },
            follow_redirects=False,
        )
        assert r.status_code in (200, 303), r.text

        # Archive
        r = client.post(f"/prospects/{prospect_id}/archive", follow_redirects=False)
        assert r.status_code in (200, 303), r.text

        # Unarchive
        r = client.post(f"/prospects/{prospect_id}/unarchive", follow_redirects=False)
        assert r.status_code in (200, 303), r.text
    finally:
        _cleanup(db, path)


def test_duplicate_blocked() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)

        # Create first prospect
        r = client.post(
            "/prospects/new",
            data={
                "business_name": "Unique Business",
                "phone_raw": "08012345678",
            },
            follow_redirects=False,
        )
        assert r.status_code in (200, 303), r.text

        # Try to create duplicate with same name (normalized_name match)
        r = client.post(
            "/prospects/new",
            data={
                "business_name": "Unique Business",
                "phone_raw": "08098765432",
            },
            follow_redirects=False,
        )
        assert r.status_code == 409, f"Expected 409, got {r.status_code}: {r.text}"
    finally:
        _cleanup(db, path)


def test_api_prospect_list() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)

        # Empty list
        r = client.get("/api/v1/prospects")
        assert r.status_code == 200
        assert r.json() == []

        # Create via API
        r = client.post(
            "/api/v1/prospects",
            json={
                "business_name": "API Test Biz",
                "category": "restaurant",
                "location_city": "Lagos",
                "email": "test@api.example",
            },
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["business_name"] == "API Test Biz"
        assert data["stage"] == "new"
        assert data["id"] == 1

        # List again
        r = client.get("/api/v1/prospects")
        assert r.status_code == 200
        assert len(r.json()) == 1

        # Get single
        r = client.get("/api/v1/prospects/1")
        assert r.status_code == 200
        assert r.json()["business_name"] == "API Test Biz"

        # Not found
        r = client.get("/api/v1/prospects/999")
        assert r.status_code == 404
    finally:
        _cleanup(db, path)


def test_stage_transition_valid_and_invalid() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)

        r = client.post(
            "/prospects/new",
            data={"business_name": "Stage Test"},
            follow_redirects=False,
        )
        assert r.status_code in (200, 303)

        # Find the created prospect via API
        r = client.get("/api/v1/prospects?q=Stage")
        assert r.status_code == 200
        pid = str(r.json()[0]["id"])

        # Valid transition: new -> researched
        r = client.post(f"/api/v1/prospects/{pid}/stage", json={"stage": "researched"})
        assert r.status_code == 200, r.text
        assert r.json()["stage"] == "researched"

        # Valid: researched -> opportunity_identified
        r = client.post(f"/api/v1/prospects/{pid}/stage", json={"stage": "opportunity_identified"})
        assert r.status_code == 200, r.text

        # Invalid: opportunity_identified -> contacted (skips outreach_ready)
        r = client.post(f"/api/v1/prospects/{pid}/stage", json={"stage": "contacted"})
        assert r.status_code == 409, f"Expected 409, got {r.status_code}"

        # Invalid: new -> do_not_contact (not a valid transition)
        r = client.post(f"/api/v1/prospects/{pid}/stage", json={"stage": "bogus"})
        assert r.status_code == 409
    finally:
        _cleanup(db, path)


def test_profile_get_and_update() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)

        # Get default profile
        r = client.get("/api/v1/settings/profile")
        assert r.status_code == 200
        assert r.json()["offer"] is None

        # Update profile
        r = client.patch(
            "/api/v1/settings/profile",
            json={
                "offer": "Web design for small businesses",
                "target_industries": ["hotel", "restaurant"],
                "target_locations": ["Ikorodu", "Lagos"],
                "ideal_signals": ["no website", "outdated site"],
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["offer"] == "Web design for small businesses"
        assert data["target_industries"] == ["hotel", "restaurant"]
        assert data["target_locations"] == ["Ikorodu", "Lagos"]
        assert data["ideal_signals"] == ["no website", "outdated site"]
    finally:
        _cleanup(db, path)


def test_prospect_detail_page() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)

        r = client.post(
            "/prospects/new",
            data={"business_name": "Detail Page Test"},
            follow_redirects=False,
        )
        assert r.status_code in (200, 303)

        # Find via API
        r = client.get("/api/v1/prospects?q=Detail")
        pid = str(r.json()[0]["id"])

        # View detail page
        r = client.get(f"/prospects/{pid}")
        assert r.status_code == 200
        assert "Detail Page Test" in r.text
    finally:
        _cleanup(db, path)


def test_profile_page() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)

        r = client.get("/settings/profile")
        assert r.status_code == 200
        assert "Profile" in r.text
    finally:
        _cleanup(db, path)
