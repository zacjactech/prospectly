"""Suppression + stage/event-transition regression tests (H2, H3, M7).

Covers the review findings:
- H2: do_not_contact is settable and clearable from both surfaces (Flow 8),
  the stage machine accepts the dispositions, and a stage move to
  do_not_contact keeps the suppression FLAG in sync (guards read the flag).
- H3: confirm-sent lands stage `contacted` and record-reply lands `replied`
  from any active stage (USER_FLOWS Flow 5.8 / 6.2 promises).
- M7: sent_confirmed messages reject edits; not_sent logs its own activity
  type instead of masquerading as draft_approved.
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
from app.models import Activity, OutreachMessage, Prospect
from app.services import outreach as outreach_service
from app.services import prospect as prospect_service
from tests.conftest import post_form, post_json


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


def _add_prospect(
    db: Session, *, stage: str = "new", phone: str | None = None, name: str | None = None
) -> Prospect:
    from app.models import Source
    from app.services.prospect import create_prospect

    source = db.query(Source).filter(Source.type == "manual").first()
    if source is None:
        source = Source(type="manual", label="Manual add", detail={})
        db.add(source)
        db.flush()
    prospect, _ = create_prospect(
        db,
        business_name=name or "Suppression Test Co",
        source=source,
        phone_raw=phone,
        phone_e164=phone,
        actor="user",
        skip_dupe_check=True,
    )
    assert prospect is not None
    prospect.stage = stage
    db.flush()
    return prospect


def _open_message(db: Session, prospect_id: int) -> OutreachMessage:
    """Draft → pending_review → approved → opened_in_whatsapp (no send)."""
    message, error = outreach_service.create_draft(
        db, prospect_id=prospect_id, current_text="Hello, we can help."
    )
    assert error is None and message is not None
    message, error = outreach_service.submit_message(
        db, message_id=message.id, current_text=message.current_text
    )
    assert error is None and message is not None
    message, error = outreach_service.approve_message(
        db, message_id=message.id, ungrounded_acknowledgement=True
    )
    assert error is None and message is not None
    message, error, wa_url = outreach_service.open_message(db, message_id=message.id)
    assert error is None and message is not None
    assert wa_url is not None and wa_url.startswith("https://wa.me/")
    return message


# ---------------------------------------------------------------------------
# H2 — suppression reachable from both surfaces, flag stays in sync
# ---------------------------------------------------------------------------


def test_html_suppress_and_unsuppress_roundtrip() -> None:
    """Flow 8: Do-not-contact button suppresses (flag + stage); clearing with
    a reason restores both."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        p = _add_prospect(db, stage="contacted")

        r = post_form(
            client,
            f"/prospects/{p.id}/do-not-contact",
            data={"reason": "opt-out received on WhatsApp"},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated is not None
        assert updated.do_not_contact is True
        assert updated.do_not_contact_reason == "opt-out received on WhatsApp"
        assert updated.stage == "do_not_contact", "stage must follow the flag"

        # Audit trail: flag set (with pre-suppression stage) + stage change.
        types = [a.type for a in db.query(Activity).filter_by(prospect_id=p.id).all()]
        assert "do_not_contact_set" in types
        assert "stage_changed" in types

        # Clearing without a reason is rejected (Flow 8: reason required).
        r = post_form(
            client,
            f"/prospects/{p.id}/do-not-contact/clear",
            data={"reason": "  "},
            follow_redirects=False,
        )
        assert r.status_code == 400, r.text

        r = post_form(
            client,
            f"/prospects/{p.id}/do-not-contact/clear",
            data={"reason": "re-confirmed consent"},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text
        db.expire_all()
        cleared = db.get(Prospect, p.id)
        assert cleared is not None
        assert cleared.do_not_contact is False
        assert cleared.do_not_contact_reason is None
        assert cleared.stage == "contacted", "stage must revert to pre-suppression"
        cleared_types = [a.type for a in db.query(Activity).filter_by(prospect_id=p.id).all()]
        assert "do_not_contact_cleared" in cleared_types
    finally:
        _cleanup(db, path)


def test_api_suppress_and_unsuppress() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        p = _add_prospect(db, stage="replied")

        r = client.post(
            f"/api/v1/prospects/{p.id}/do-not-contact",
            json={"reason": "asked to stop"},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["do_not_contact"] is True
        assert body["stage"] == "do_not_contact"

        # Unsuppress without reason → 400 (Flow 8).
        r = client.post(
            f"/api/v1/prospects/{p.id}/do-not-contact/clear",
            json={"reason": ""},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 400, r.text

        r = client.post(
            f"/api/v1/prospects/{p.id}/do-not-contact/clear",
            json={"reason": "mistake"},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["do_not_contact"] is False
        assert body["stage"] == "replied", "stage must revert"
    finally:
        _cleanup(db, path)


def test_stage_move_to_do_not_contact_sets_flag() -> None:
    """A stage change to do_not_contact must set the suppression flag too —
    otherwise the prospect displays as suppressed while guards never fire."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        p = _add_prospect(db, stage="replied")

        r = post_json(client, f"/api/v1/prospects/{p.id}/stage", data={"stage": "do_not_contact"})
        assert r.status_code == 200, r.text
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated is not None
        assert updated.stage == "do_not_contact"
        assert updated.do_not_contact is True, "flag must follow the stage move"

        # Outbound is now blocked (guards read the flag).
        message, error = outreach_service.create_draft(
            db, prospect_id=p.id, current_text="should be blocked"
        )
        assert message is None
        assert error is not None and "suppressed" in error
    finally:
        _cleanup(db, path)


def test_not_interested_is_reachable_and_terminal_for_outreach() -> None:
    """Flow 6.3: not_interested settable from an active stage; outreach is
    terminal afterwards."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        p = _add_prospect(db, stage="contacted")

        r = post_json(client, f"/api/v1/prospects/{p.id}/stage", data={"stage": "not_interested"})
        assert r.status_code == 200, r.text
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated is not None and updated.stage == "not_interested"
        # not a suppression: flag untouched
        assert updated.do_not_contact is False

        # Terminal for outreach: no new drafts, no move back into the funnel.
        message, error = outreach_service.create_draft(
            db, prospect_id=p.id, current_text="one more try"
        )
        assert message is None
        assert error is not None and "not_interested" in error

        r = post_json(client, f"/api/v1/prospects/{p.id}/stage", data={"stage": "contacted"})
        assert r.status_code == 409, r.text
    finally:
        _cleanup(db, path)


def test_suppression_reachable_from_html_detail_page() -> None:
    """The detail page renders the Do-not-contact / Clear forms (Flow 8 UI)."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        p = _add_prospect(db, stage="contacted")
        r = client.get(f"/prospects/{p.id}")
        assert r.status_code == 200
        assert f'action="/prospects/{p.id}/do-not-contact"' in r.text
        assert "Do not contact" in r.text

        prospect_service.set_do_not_contact(db, p.id, reason="x")
        db.commit()
        r = client.get(f"/prospects/{p.id}")
        assert r.status_code == 200
        assert f'action="/prospects/{p.id}/do-not-contact/clear"' in r.text
        assert "Clear suppression" in r.text
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# H3 — confirm-sent → contacted, record-reply → replied, from any active stage
# ---------------------------------------------------------------------------


def test_confirm_sent_advances_stage_from_opportunity_identified() -> None:
    """Flow 5.8: confirm-sent must land stage contacted even when the
    pipeline position lags (no manual outreach_ready hop)."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, stage="opportunity_identified", phone="442079460958")
        message = _open_message(db, p.id)
        message, error = outreach_service.confirm_sent(db, message_id=message.id)
        assert error is None and message is not None
        assert message.status == "sent_confirmed"
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated is not None
        assert updated.stage == "contacted", "Flow 5.8: stage must become contacted"
    finally:
        _cleanup(db, path)


def test_confirm_sent_keeps_follow_up_due_stage() -> None:
    """A re-contact on a follow_up_due prospect must not silently clear the
    follow-up state."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, stage="follow_up_due", phone="442079460958")
        message = _open_message(db, p.id)
        message, error = outreach_service.confirm_sent(db, message_id=message.id)
        assert error is None
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated is not None
        assert updated.stage == "follow_up_due"
    finally:
        _cleanup(db, path)


def test_record_reply_advances_stage_from_active_stages() -> None:
    """Flow 6.2: record reply → stage replied, from any active stage."""
    db, path = _make_db()
    try:
        for start_stage in ("opportunity_identified", "contacted", "follow_up_due", "new"):
            p = _add_prospect(db, stage=start_stage, name=f"Reply Stage {start_stage}")
            prospect, error = outreach_service.record_reply(
                db, prospect_id=p.id, text="interested, send details"
            )
            assert error is None, error
            db.expire_all()
            updated = db.get(Prospect, p.id)
            assert updated is not None, start_stage
            assert updated.stage == "replied", f"from {start_stage}: {updated.stage}"
    finally:
        _cleanup(db, path)


def test_record_reply_does_not_overwrite_terminal_dispositions() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, stage="not_interested")
        _, error = outreach_service.record_reply(db, prospect_id=p.id, text="ok")
        assert error is None  # the reply is still recorded...
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated is not None
        assert updated.stage == "not_interested", "...but the disposition stays"
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# M7 — sent_confirmed is terminal for edits; not_sent logs its own type
# ---------------------------------------------------------------------------


def test_edit_rejects_sent_confirmed_message() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, stage="outreach_ready", phone="442079460958")
        message = _open_message(db, p.id)
        message, error = outreach_service.confirm_sent(db, message_id=message.id)
        assert error is None and message is not None
        confirmed_at = message.sent_confirmed_at
        assert confirmed_at is not None

        edited, error = outreach_service.edit_message(
            db, message_id=message.id, current_text="rewritten history"
        )
        assert edited is None
        assert error is not None and "confirmed sent" in error

        db.expire_all()
        fresh = db.get(OutreachMessage, message.id)
        assert fresh is not None
        assert fresh.status == "sent_confirmed", "terminal state must survive"
        # SQLite round-trips datetimes naive; compare wall-clock values.
        confirmed_wall = confirmed_at.replace(tzinfo=None) if confirmed_at.tzinfo else confirmed_at
        fresh_wall = (
            fresh.sent_confirmed_at.replace(tzinfo=None)
            if fresh.sent_confirmed_at and fresh.sent_confirmed_at.tzinfo
            else fresh.sent_confirmed_at
        )
        assert fresh_wall == confirmed_wall, "confirmation must survive"
        assert fresh.current_text != "rewritten history"
    finally:
        _cleanup(db, path)


def test_not_sent_logs_message_not_sent_activity() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, stage="outreach_ready", phone="442079460958")
        message = _open_message(db, p.id)
        message, error = outreach_service.not_sent(db, message_id=message.id)
        assert error is None and message is not None
        assert message.status == "approved"

        types = [a.type for a in db.query(Activity).filter_by(prospect_id=p.id).all()]
        assert "message_not_sent" in types, types
        masquerade = [
            a
            for a in db.query(Activity).filter_by(prospect_id=p.id, type="draft_approved")
            if (a.payload or {}).get("reset_from") == "opened_in_whatsapp"
        ]
        assert masquerade == [], "not_sent must not log as draft_approved"
    finally:
        _cleanup(db, path)


def test_html_edit_of_sent_confirmed_returns_409() -> None:
    """Route-level: editing a confirmed-sent message is a 409, not a silent
    history rewrite."""
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        p = _add_prospect(db, stage="outreach_ready", phone="442079460958")
        message = _open_message(db, p.id)
        message, error = outreach_service.confirm_sent(db, message_id=message.id)
        assert error is None and message is not None

        r = post_form(
            client,
            f"/messages/{message.id}/edit",
            data={"current_text": "rewritten"},
            follow_redirects=False,
        )
        assert r.status_code == 409, r.text
    finally:
        _cleanup(db, path)
