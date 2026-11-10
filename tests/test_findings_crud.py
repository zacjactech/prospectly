"""Findings evidence CRUD tests (USER_FLOWS P8, DATABASE_DESIGN §2.6, P5/P7).

Covers the service invariants (forced human origin, duplicate safety, verify
idempotency, edit audit, delete guard) plus HTML-route and JSON-API parity.
No network/provider calls: findings are created/edited directly in a temp DB.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import Activity, Finding, Opportunity, OpportunityFinding, Prospect
from app.services import findings as findings_service
from app.services import research as research_service
from tests.conftest import post_form, post_json


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    return db, path


def _client(db: Session) -> TestClient:
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


def _add_prospect(db: Session, name: str = "Evidence Co") -> Prospect:
    prospect = Prospect(business_name=name, normalized_name=name.lower(), stage="researched")
    db.add(prospect)
    db.flush()
    return prospect


def _add_ai_finding(
    db: Session,
    *,
    prospect_id: int,
    statement: str = "AI says the shop has no menu",
    kind: str = "identity",
) -> Finding:
    finding = Finding(
        prospect_id=prospect_id,
        kind=kind,
        statement=statement,
        source_type="ai_inference",
        retrieved_at=datetime.now(UTC),
        verification_status="unverified",
        confidence="low",
        origin="ai",
        finding_key=research_service._finding_key(kind, statement),
    )
    db.add(finding)
    db.flush()
    return finding


# ---------------------------------------------------------------------------
# Service: create
# ---------------------------------------------------------------------------


def test_create_forces_human_origin_and_defaults() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding, error = findings_service.create_finding(
            db,
            prospect_id=prospect.id,
            kind="  observation  ",
            statement="  The shop has no online menu  ",
            detail="  saw it on the door  ",
            source_url="  https://example.com/x  ",
        )
        assert error is None
        assert finding is not None
        assert finding.kind == "observation"
        assert finding.statement == "The shop has no online menu"
        assert finding.detail == "saw it on the door"
        assert finding.source_url == "https://example.com/x"
        assert finding.origin == "human"
        assert finding.verification_status == "unverified"
        assert finding.confidence == "low"
        assert finding.source_type == "user"
        assert finding.research_run_id is None
        assert finding.retrieved_at is not None
        assert finding.finding_key == research_service._finding_key(
            "observation", "The shop has no online menu"
        )
        activities = db.query(Activity).filter_by(prospect_id=prospect.id).all()
        assert [a.type for a in activities] == ["finding_added"]
        assert activities[0].payload["finding_id"] == finding.id
    finally:
        _cleanup(db, path)


def test_create_duplicate_returns_error_not_integrity_error() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        first, error = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="website", statement="No menu page"
        )
        assert error is None and first is not None
        # Same normalized statement → same finding_key.
        duplicate, error = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="website", statement="  no   MENU page "
        )
        assert duplicate is None
        assert error == findings_service.DUPLICATE_ERROR
        assert db.query(Finding).filter_by(prospect_id=prospect.id).count() == 1
    finally:
        _cleanup(db, path)


def test_create_validates_required_fields() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        assert findings_service.create_finding(
            db, prospect_id=prospect.id, kind="", statement="x"
        ) == (None, "Kind is required")
        assert findings_service.create_finding(
            db, prospect_id=prospect.id, kind="x", statement="   "
        ) == (None, "Statement is required")
        assert (
            findings_service.create_finding(
                db, prospect_id=prospect.id, kind="k" * 41, statement="x"
            )[1]
            is not None
        )
        assert (
            findings_service.create_finding(
                db, prospect_id=prospect.id, kind="x", statement="s" * 501
            )[1]
            is not None
        )
        assert findings_service.create_finding(
            db, prospect_id=9999, kind="x", statement="valid"
        ) == (None, "Prospect not found")
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# Service: verify
# ---------------------------------------------------------------------------


def test_verify_sets_status_and_is_idempotent() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="observation", statement="Manual claim"
        )
        assert finding is not None

        verified, error = findings_service.verify_finding(db, finding_id=finding.id)
        assert error is None
        assert verified is not None and verified.verification_status == "verified"
        verified_acts = (
            db.query(Activity).filter_by(prospect_id=prospect.id, type="finding_verified").all()
        )
        assert len(verified_acts) == 1

        again, error = findings_service.verify_finding(db, finding_id=finding.id)
        assert error is None and again is not None
        assert again.verification_status == "verified"
        assert (
            db.query(Activity).filter_by(prospect_id=prospect.id, type="finding_verified").count()
            == 1
        )
        assert findings_service.verify_finding(db, finding_id=9999) == (
            None,
            "Finding not found",
        )
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# Service: edit
# ---------------------------------------------------------------------------


def test_edit_ai_finding_preserves_original_statement_on_first_edit_only() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding = _add_ai_finding(db, prospect_id=prospect.id, statement="AI claim")

        edited, error = findings_service.edit_finding(
            db, finding_id=finding.id, statement="Human correction", detail="detail"
        )
        assert error is None and edited is not None
        assert edited.origin == "ai"
        assert edited.original_statement == "AI claim"
        assert edited.edited_by == "user"
        assert edited.finding_key == research_service._finding_key("identity", "Human correction")

        edited2, error = findings_service.edit_finding(
            db, finding_id=finding.id, statement="Second correction"
        )
        assert error is None and edited2 is not None
        assert edited2.original_statement == "AI claim", "stored original must never be overwritten"

        payload = (
            db.query(Activity)
            .filter_by(prospect_id=prospect.id, type="finding_edited")
            .order_by(Activity.id.desc())
            .first()
        )
        assert payload is not None
        assert payload.payload["from_statement"] == "Human correction"
        assert payload.payload["to_statement"] == "Second correction"
    finally:
        _cleanup(db, path)


def test_edit_human_finding_sets_edited_by_without_original() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="observation", statement="Original human claim"
        )
        assert finding is not None
        edited, error = findings_service.edit_finding(
            db, finding_id=finding.id, statement="Edited human claim", detail="new detail"
        )
        assert error is None and edited is not None
        assert edited.origin == "human"
        assert edited.edited_by == "user"
        assert edited.original_statement is None
        assert edited.statement == "Edited human claim"
        assert edited.detail == "new detail"
    finally:
        _cleanup(db, path)


def test_edit_statement_collision_is_rejected() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        first, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="website", statement="Alpha claim"
        )
        second, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="website", statement="Beta claim"
        )
        assert first is not None and second is not None

        result, error = findings_service.edit_finding(
            db, finding_id=second.id, statement="alpha   CLAIM"
        )
        assert result is None
        assert error == findings_service.DUPLICATE_ERROR
        db.expire_all()
        unchanged = db.get(Finding, second.id)
        assert unchanged is not None and unchanged.statement == "Beta claim"
    finally:
        _cleanup(db, path)


def test_edit_validates_statement_and_not_found() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="observation", statement="A claim"
        )
        assert finding is not None
        assert findings_service.edit_finding(db, finding_id=finding.id, statement="   ") == (
            None,
            "Statement is required",
        )
        assert findings_service.edit_finding(db, finding_id=9999, statement="x") == (
            None,
            "Finding not found",
        )
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# Service: delete
# ---------------------------------------------------------------------------


def test_delete_removes_row_and_keeps_audit_payload() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="observation", statement="Deletable claim"
        )
        assert finding is not None
        finding_id = finding.id

        deleted, error = findings_service.delete_finding(db, finding_id=finding_id)
        assert error is None
        assert deleted is not None
        assert db.get(Finding, finding_id) is None

        activity = (
            db.query(Activity).filter_by(prospect_id=prospect.id, type="finding_deleted").one()
        )
        assert activity.payload["statement"] == "Deletable claim"
        assert activity.payload["origin"] == "human"
    finally:
        _cleanup(db, path)


def test_delete_linked_to_opportunity_is_blocked() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="observation", statement="Evidence claim"
        )
        assert finding is not None
        opportunity = Opportunity(
            prospect_id=prospect.id,
            type="other",
            summary="Backed by evidence",
            status="proposed",
            origin="human",
        )
        db.add(opportunity)
        db.flush()
        db.add(OpportunityFinding(opportunity_id=opportunity.id, finding_id=finding.id))
        db.flush()

        result, error = findings_service.delete_finding(db, finding_id=finding.id)
        assert result is None
        assert error == findings_service.LINKED_ERROR
        assert db.get(Finding, finding.id) is not None
        assert findings_service.delete_finding(db, finding_id=9999) == (
            None,
            "Finding not found",
        )
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# HTML routes
# ---------------------------------------------------------------------------


def test_html_finding_full_cycle() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        db.commit()
        client = _client(db)

        # Create.
        r = post_form(
            client,
            f"/prospects/{prospect.id}/findings",
            data={
                "kind": "observation",
                "statement": "Manual evidence",
                "detail": "noted on site",
                "source_url": "",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text
        db.expire_all()
        finding = db.query(Finding).filter_by(prospect_id=prospect.id).one()
        assert finding.origin == "human"

        # Verify.
        r = post_form(client, f"/findings/{finding.id}/verify", follow_redirects=False)
        assert r.status_code == 303, r.text
        db.expire_all()
        assert db.get(Finding, finding.id).verification_status == "verified"

        # Edit.
        r = post_form(
            client,
            f"/findings/{finding.id}/edit",
            data={"statement": "Manual evidence edited", "detail": "updated"},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text
        db.expire_all()
        edited = db.get(Finding, finding.id)
        assert edited.statement == "Manual evidence edited"
        assert edited.detail == "updated"

        # Duplicate create → 409.
        r = post_form(
            client,
            f"/prospects/{prospect.id}/findings",
            data={"kind": "observation", "statement": "  manual EVIDENCE edited "},
            follow_redirects=False,
        )
        assert r.status_code == 409, r.text

        # Empty statement → 400.
        r = post_form(
            client,
            f"/prospects/{prospect.id}/findings",
            data={"kind": "observation", "statement": "   "},
            follow_redirects=False,
        )
        assert r.status_code == 400, r.text

        # Delete.
        r = post_form(client, f"/findings/{finding.id}/delete", follow_redirects=False)
        assert r.status_code == 303, r.text
        db.expire_all()
        assert db.get(Finding, finding.id) is None
    finally:
        _cleanup(db, path)


@pytest.mark.parametrize(
    "url,data",
    [
        ("/findings/9999/edit", {"statement": "x"}),
        ("/findings/9999/verify", {}),
        ("/findings/9999/delete", {}),
    ],
)
def test_html_finding_routes_not_found(url: str, data: dict) -> None:
    db, path = _make_db()
    try:
        db.commit()
        client = _client(db)
        r = post_form(client, url, data=data, follow_redirects=False)
        assert r.status_code == 404, r.text
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# JSON API parity
# ---------------------------------------------------------------------------


def test_api_findings_crud_parity() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        db.commit()
        client = _client(db)

        # Create: client-supplied origin/verification/confidence are ignored.
        r = post_json(
            client,
            f"/api/v1/prospects/{prospect.id}/findings",
            {
                "kind": "observation",
                "statement": "API evidence",
                "detail": "detail",
                "origin": "ai",
                "verification_status": "verified",
                "confidence": "high",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["origin"] == "human"
        assert body["verification_status"] == "unverified"
        assert body["confidence"] == "low"
        finding_id = body["id"]

        # Duplicate → 409.
        r = post_json(
            client,
            f"/api/v1/prospects/{prospect.id}/findings",
            {"kind": "observation", "statement": "api   EVIDENCE"},
        )
        assert r.status_code == 409, r.text

        # Empty fields → 400.
        r = post_json(
            client,
            f"/api/v1/prospects/{prospect.id}/findings",
            {"kind": "observation", "statement": "   "},
        )
        assert r.status_code == 400, r.text

        # Missing prospect → 404.
        r = post_json(
            client,
            "/api/v1/prospects/9999/findings",
            {"kind": "observation", "statement": "orphan"},
        )
        assert r.status_code == 404, r.text

        # Edit.
        r = post_json(
            client,
            f"/api/v1/findings/{finding_id}/edit",
            {"statement": "API evidence edited", "detail": "new"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["statement"] == "API evidence edited"

        # Verify (twice: idempotent).
        r = post_json(client, f"/api/v1/findings/{finding_id}/verify")
        assert r.status_code == 200, r.text
        assert r.json()["verification_status"] == "verified"
        r = post_json(client, f"/api/v1/findings/{finding_id}/verify")
        assert r.status_code == 200, r.text

        # Delete → 204, then 404.
        r = post_json(client, f"/api/v1/findings/{finding_id}/delete")
        assert r.status_code == 204, r.text
        r = post_json(client, f"/api/v1/findings/{finding_id}/delete")
        assert r.status_code == 404, r.text
    finally:
        _cleanup(db, path)


def test_api_delete_linked_finding_returns_409() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="observation", statement="Backed claim"
        )
        assert finding is not None
        opportunity = Opportunity(
            prospect_id=prospect.id,
            type="other",
            summary="Needs evidence",
            status="proposed",
            origin="human",
        )
        db.add(opportunity)
        db.flush()
        db.add(OpportunityFinding(opportunity_id=opportunity.id, finding_id=finding.id))
        db.commit()
        client = _client(db)

        r = post_json(client, f"/api/v1/findings/{finding.id}/delete")
        assert r.status_code == 409, r.text
    finally:
        _cleanup(db, path)


def test_api_edit_collision_returns_409() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        first, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="website", statement="First claim"
        )
        second, _ = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="website", statement="Second claim"
        )
        assert first is not None and second is not None
        db.commit()
        client = _client(db)

        r = post_json(
            client,
            f"/api/v1/findings/{second.id}/edit",
            {"statement": "first CLAIM"},
        )
        assert r.status_code == 409, r.text
    finally:
        _cleanup(db, path)


def test_api_list_findings_filters() -> None:
    """API_DESIGN §2.3: list supports kind / verification_status / origin filters."""
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, "Filter Co")
        created, error = findings_service.create_finding(
            db, prospect_id=prospect.id, kind="identity", statement="Owner is Ada"
        )
        assert error is None and created is not None
        _add_ai_finding(db, prospect_id=prospect.id, kind="website", statement="Site has no menu")
        db.commit()
        client = _client(db)
        headers = {"X-CSRF-Token": client.cookies.get("csrf_token", "")}

        r = client.get(f"/api/v1/prospects/{prospect.id}/findings")
        assert r.status_code == 200 and len(r.json()) == 2, r.text

        r = client.get(f"/api/v1/prospects/{prospect.id}/findings", params={"kind": "website"})
        assert [f["kind"] for f in r.json()] == ["website"], r.text

        r = client.get(f"/api/v1/prospects/{prospect.id}/findings", params={"origin": "ai"})
        assert [f["origin"] for f in r.json()] == ["ai"], r.text

        r = client.post(f"/api/v1/findings/{created.id}/verify", headers=headers)
        assert r.status_code == 200, r.text
        r = client.get(
            f"/api/v1/prospects/{prospect.id}/findings",
            params={"verification_status": "verified"},
        )
        assert [f["id"] for f in r.json()] == [created.id], r.text
    finally:
        _cleanup(db, path)
