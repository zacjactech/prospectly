"""Outreach message state-machine tests (Flow 5, SYSTEM_ARCHITECTURE §3.1).

Golden path: draft → submit → approve → open (wa.me URL asserted) →
confirm sent → sent_confirmed + activity + last_contacted_at + stage
contacted. Illegal transitions and suppression guards return errors.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import Activity, Finding, Prospect
from app.services.outreach import (
    approve_message,
    confirm_sent,
    create_draft,
    edit_message,
    generate_draft,
    not_sent,
    open_message,
    record_reply,
    submit_message,
)


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    return db, path


def _add_prospect(
    db: Session, *, stage: str = "outreach_ready", phone: str | None = None
) -> Prospect:
    prospect = Prospect(
        business_name="Acme Widgets",
        normalized_name="acme widgets",
        stage=stage,
        phone_e164=phone,
    )
    db.add(prospect)
    db.flush()
    return prospect


def _add_finding(db: Session, *, prospect_id: int, statement: str) -> Finding:
    key = hashlib.sha256(f"website:{statement}".encode()).hexdigest()[:32]
    finding = Finding(
        prospect_id=prospect_id,
        kind="website",
        statement=statement,
        source_type="website_fetch",
        verification_status="verified",
        confidence="high",
        origin="provider",
        finding_key=key,
        retrieved_at=datetime.now(UTC),
    )
    db.add(finding)
    db.flush()
    return finding


def _grounded_draft(db: Session, prospect_id: int):
    _add_finding(db, prospect_id=prospect_id, statement="No website found")
    message, error = generate_draft(db, prospect_id=prospect_id)
    assert error is None
    return message


def test_golden_path() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, stage="outreach_ready", phone="442079460958")
        message = _grounded_draft(db, prospect.id)

        # Submit → pending_review.
        message, error = submit_message(
            db, message_id=message.id, current_text=message.current_text
        )
        assert error is None
        assert message.status == "pending_review"

        # Approve (grounded via generate_draft).
        message, error = approve_message(db, message_id=message.id)
        assert error is None
        assert message.status == "approved"
        assert message.approved_at is not None

        # Open → wa.me URL asserted, status opened_in_whatsapp (never sent).
        message, error, wa_me_url = open_message(db, message_id=message.id)
        assert error is None
        assert message.status == "opened_in_whatsapp"
        from app.whatsapp import build_wa_me_url

        assert wa_me_url == build_wa_me_url("442079460958", message.current_text)

        # Confirm sent → sent_confirmed + activity + last_contacted_at + stage contacted.
        message, error = confirm_sent(db, message_id=message.id)
        assert error is None
        assert message.status == "sent_confirmed"
        assert message.sent_confirmed_at is not None
        db.expire_all()
        updated = db.get(Prospect, prospect.id)
        assert updated.last_contacted_at is not None
        assert updated.stage == "contacted"

        activities = (
            db.query(Activity)
            .filter_by(prospect_id=prospect.id, type="message_sent_confirmed")
            .all()
        )
        assert len(activities) == 1
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_not_sent_returns_to_approved() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, stage="outreach_ready", phone="442079460958")
        message = _grounded_draft(db, prospect.id)
        message, _ = submit_message(db, message_id=message.id, current_text=message.current_text)
        message, _ = approve_message(db, message_id=message.id)
        message, _, _ = open_message(db, message_id=message.id)
        assert message.status == "opened_in_whatsapp"

        message, error = not_sent(db, message_id=message.id)
        assert error is None
        assert message.status == "approved"
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_edit_demotes_to_draft() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, stage="outreach_ready", phone="442079460958")
        message = _grounded_draft(db, prospect.id)
        message, _ = submit_message(db, message_id=message.id, current_text=message.current_text)
        message, _ = approve_message(db, message_id=message.id)
        assert message.status == "approved"

        message, error = edit_message(db, message_id=message.id, current_text="Revised")
        assert error is None
        assert message.status == "draft"
        assert message.current_text == "Revised"
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_suppression_blocks_outbound() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, stage="outreach_ready", phone="442079460958")

        # Draft + submit while NOT suppressed.
        message, error = create_draft(db, prospect_id=prospect.id, current_text="Hello")
        assert error is None
        message, error = submit_message(db, message_id=message.id, current_text="Hello")
        assert error is None
        assert message.status == "pending_review"

        # Now suppress.
        from app.services.prospect import set_do_not_contact

        set_do_not_contact(db, prospect.id, reason="opted out", actor="user")

        # Approve blocked by suppression.
        message, error = approve_message(db, message_id=message.id)
        assert error is not None
        assert "do-not-contact" in error.lower()

        # A new draft is also blocked after suppression.
        message, error = create_draft(db, prospect_id=prospect.id, current_text="Again")
        assert error is not None
        assert message is None
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_approve_blocked_without_grounding_without_ack() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, stage="outreach_ready", phone="442079460958")
        message, _ = create_draft(
            db,
            prospect_id=prospect.id,
            current_text="Ungrounded pitch",
            origin="human",
            grounding=None,
        )
        message, _ = submit_message(db, message_id=message.id, current_text="Ungrounded pitch")
        message_id = message.id
        message, error = approve_message(db, message_id=message_id)
        assert error is not None
        assert "grounded" in error.lower()

        # With the acknowledgement it passes (message still pending_review).
        message, error = approve_message(db, message_id=message_id, ungrounded_acknowledgement=True)
        assert error is None
        assert message.status == "approved"
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)


def test_record_reply_moves_stage_to_replied() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, stage="contacted")
        prospect, error = record_reply(db, prospect_id=prospect.id, text="Thanks, I'm interested")
        assert error is None
        db.expire_all()
        assert db.get(Prospect, prospect.id).stage == "replied"
    finally:
        db.close()
        with suppress(OSError):
            os.remove(path)
