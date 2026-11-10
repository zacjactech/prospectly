"""Notes surface tests (Flow 9 handoff note, UI spec §2.3, DATABASE_DESIGN §2.4).

The app exposes HTML notes only — API_DESIGN deliberately promises no notes
API. Tests cover the service (validation, activity logging) and both routes.
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
from app.models import Activity, Note, Prospect
from app.services import notes as notes_service
from tests.conftest import post_form


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
    client.get("/")
    return client


def _cleanup(db: Session, path: str) -> None:
    db.close()
    app.dependency_overrides.clear()
    with suppress(OSError):
        os.unlink(path)


def _add_prospect(db: Session, name: str = "Notes Co") -> Prospect:
    prospect = Prospect(business_name=name, normalized_name=name.lower(), stage="converted")
    db.add(prospect)
    db.flush()
    return prospect


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def test_add_note_strips_body_and_logs_activity() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        note, error = notes_service.add_note(
            db, prospect_id=prospect.id, body="  Agreed on the Q1 retainer  "
        )
        assert error is None
        assert note is not None
        assert note.body == "Agreed on the Q1 retainer"
        activity = db.query(Activity).filter_by(prospect_id=prospect.id).one()
        assert activity.type == "note_added"
        assert activity.payload["note_id"] == note.id
    finally:
        _cleanup(db, path)


def test_add_note_validation_and_not_found() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        assert notes_service.add_note(db, prospect_id=prospect.id, body="   ") == (
            None,
            "Note body is required",
        )
        assert notes_service.add_note(db, prospect_id=prospect.id, body="x" * 2001)[1] is not None
        assert notes_service.add_note(db, prospect_id=9999, body="valid") == (
            None,
            "Prospect not found",
        )
    finally:
        _cleanup(db, path)


def test_edit_note_updates_body_and_logs_truncated_payload() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        note, _ = notes_service.add_note(db, prospect_id=prospect.id, body="Original body")
        assert note is not None

        long_body = "updated " * 40  # 320 chars
        edited, error = notes_service.edit_note(db, note_id=note.id, body=long_body)
        assert error is None and edited is not None
        assert edited.body == long_body.strip()

        activity = db.query(Activity).filter_by(prospect_id=prospect.id, type="note_edited").one()
        assert activity.payload["note_id"] == note.id
        assert activity.payload["from"] == "Original body"
        assert len(activity.payload["to"]) == 200

        assert notes_service.edit_note(db, note_id=note.id, body="  ") == (
            None,
            "Note body is required",
        )
        assert notes_service.edit_note(db, note_id=9999, body="x") == (
            None,
            "Note not found",
        )
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# HTML routes + detail rendering
# ---------------------------------------------------------------------------


def test_detail_page_has_notes_card_and_empty_state() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        db.commit()
        client = _client(db)
        r = client.get(f"/prospects/{prospect.id}")
        assert r.status_code == 200
        assert "Notes" in r.text
        assert "No notes yet." in r.text
        assert f'action="/prospects/{prospect.id}/notes"' in r.text
    finally:
        _cleanup(db, path)


def test_html_add_note_renders_on_detail_with_activity() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        db.commit()
        client = _client(db)

        r = post_form(
            client,
            f"/prospects/{prospect.id}/notes",
            data={"body": "Handoff: scope agreed, invoice to follow"},
            follow_redirects=True,
        )
        assert r.status_code == 200, r.text
        assert "Handoff: scope agreed, invoice to follow" in r.text
        assert "No notes yet." not in r.text

        db.expire_all()
        note = db.query(Note).filter_by(prospect_id=prospect.id).one()
        assert note.body == "Handoff: scope agreed, invoice to follow"
        assert db.query(Activity).filter_by(prospect_id=prospect.id, type="note_added").count() == 1
        assert f'action="/notes/{note.id}/edit"' in r.text
    finally:
        _cleanup(db, path)


def test_html_edit_note_updates_body() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        note, _ = notes_service.add_note(db, prospect_id=prospect.id, body="First version")
        assert note is not None
        db.commit()
        client = _client(db)

        r = post_form(
            client,
            f"/notes/{note.id}/edit",
            data={"body": "Second version"},
            follow_redirects=True,
        )
        assert r.status_code == 200, r.text
        assert "Second version" in r.text

        db.expire_all()
        assert db.get(Note, note.id).body == "Second version"
        assert (
            db.query(Activity).filter_by(prospect_id=prospect.id, type="note_edited").count() == 1
        )
    finally:
        _cleanup(db, path)


def test_html_add_note_empty_body_returns_400() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        db.commit()
        client = _client(db)
        r = post_form(
            client,
            f"/prospects/{prospect.id}/notes",
            data={"body": "   "},
            follow_redirects=False,
        )
        assert r.status_code == 400, r.text
        assert db.query(Note).count() == 0
    finally:
        _cleanup(db, path)


def test_html_edit_note_empty_body_returns_400() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        note, _ = notes_service.add_note(db, prospect_id=prospect.id, body="Keep me")
        assert note is not None
        db.commit()
        client = _client(db)
        r = post_form(client, f"/notes/{note.id}/edit", data={"body": "  "}, follow_redirects=False)
        assert r.status_code == 400, r.text
        db.expire_all()
        assert db.get(Note, note.id).body == "Keep me"
    finally:
        _cleanup(db, path)


@pytest.mark.parametrize(
    "url,data,status",
    [
        ("/prospects/9999/notes", {"body": "orphan"}, 404),
        ("/notes/9999/edit", {"body": "orphan"}, 404),
    ],
)
def test_notes_routes_not_found(url: str, data: dict, status: int) -> None:
    db, path = _make_db()
    try:
        db.commit()
        client = _client(db)
        r = post_form(client, url, data=data, follow_redirects=False)
        assert r.status_code == status, r.text
    finally:
        _cleanup(db, path)
