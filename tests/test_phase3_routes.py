"""Phase 3 route integration tests: outreach state machine (Flow 5).

Golden path through the HTML routes:
create prospect → generate draft → submit → approve → open (wa.me URL) →
confirm sent → stage contacted + last_contacted_at + activity.

Illegal transitions and suppression guards return 409.
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
from app.models import Activity, Prospect


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


def _create_prospect(client: TestClient, db: Session, phone="+442079460958") -> int:
    r = client.post(
        "/prospects/new",
        data={"business_name": "Acme Widgets", "phone_raw": phone},
        follow_redirects=False,
    )
    assert r.status_code == 303, (r.status_code, r.text)
    prospect_id = int(r.headers["location"].rsplit("/", 1)[1])
    assert db.get(Prospect, prospect_id).phone_e164 == "442079460958"
    return prospect_id


def _message_id(client: TestClient, prospect_id: int) -> int:
    r = client.get(f"/prospects/{prospect_id}")
    m = re.search(r'href="/messages/(\d+)/edit"', r.text)
    assert m, "no message edit link on detail page"
    return int(m.group(1))


def _set_stage(client: TestClient, db: Session, prospect_id: int, stage: str) -> None:
    from app.services import prospect as prospect_service

    # Walk the state machine one step at a time up to the target.
    path = ["new", "researched", "opportunity_identified", "outreach_ready"]
    current = db.get(Prospect, prospect_id).stage
    start = path.index(current) if current in path else 0
    for target in path[start + 1 : path.index(stage) + 1]:
        prospect, error = prospect_service.transition_stage(db, prospect_id, target)
        assert error is None, error
    db.flush()


def test_outreach_golden_path() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        prospect_id = _create_prospect(client, db)
        _set_stage(client, db, prospect_id, "outreach_ready")

        # Generate draft.
        r = client.post(f"/prospects/{prospect_id}/draft/generate", follow_redirects=False)
        assert r.status_code == 303

        message_id = _message_id(client, prospect_id)

        # Editor loads; gated button is "Submit for review".
        r = client.get(f"/messages/{message_id}/edit")
        assert r.status_code == 200
        assert "Submit for review" in r.text

        # Submit → pending_review.
        r = client.post(
            f"/messages/{message_id}/submit",
            data={"current_text": "Hi Acme Widgets, worth a quick chat?"},
            follow_redirects=False,
        )
        assert r.status_code == 303

        # Approve without grounding + without acknowledgement → 409.
        r = client.post(f"/messages/{message_id}/approve", follow_redirects=False)
        assert r.status_code == 409

        # Approve with acknowledgement → approved.
        r = client.post(
            f"/messages/{message_id}/approve",
            data={"ungrounded_acknowledgement": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303

        # Open → wa.me URL shown; status opened_in_whatsapp (never sent).
        r = client.post(f"/messages/{message_id}/open", follow_redirects=False)
        assert r.status_code == 200
        assert "https://wa.me/442079460958" in r.text
        assert "I have sent this message" in r.text

        # Confirm sent → sent_confirmed.
        r = client.post(f"/messages/{message_id}/confirm-sent", follow_redirects=False)
        assert r.status_code == 303

        # Side effects: stage contacted, last_contacted_at set, activity logged.
        db.expire_all()
        prospect = db.get(Prospect, prospect_id)
        assert prospect.stage == "contacted"
        assert prospect.last_contacted_at is not None
        activities = (
            db.query(Activity)
            .filter_by(prospect_id=prospect_id, type="message_sent_confirmed")
            .all()
        )
        assert len(activities) == 1

        # Not-sent path is unavailable once confirmed (status != opened_in_whatsapp).
        r = client.post(f"/messages/{message_id}/not-sent", follow_redirects=False)
        assert r.status_code == 409
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)
        app.dependency_overrides.pop(get_db, None)


def test_suppression_blocks_outbound() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        from app.services.prospect import set_do_not_contact

        prospect_id = _create_prospect(client, db)
        set_do_not_contact(db, prospect_id, reason="opted out", actor="user")

        r = client.post(f"/prospects/{prospect_id}/draft/generate", follow_redirects=False)
        assert r.status_code == 409
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)
        app.dependency_overrides.pop(get_db, None)


def test_illegal_transition_returns_409() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        prospect_id = _create_prospect(client, db)

        # Cannot confirm-sent a message that was never opened.
        r = client.post(f"/prospects/{prospect_id}/draft/generate", follow_redirects=False)
        assert r.status_code == 303
        message_id = _message_id(client, prospect_id)

        r = client.post(f"/messages/{message_id}/confirm-sent", follow_redirects=False)
        assert r.status_code == 409
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)
        app.dependency_overrides.pop(get_db, None)
