"""Outreach message state machine (Flow 5, SYSTEM_ARCHITECTURE §3.1).

Statuses: draft → pending_review → approved → opened_in_whatsapp → sent_confirmed.

Only ``approve``/``open``/``confirm_sent`` advance the chain; every edit
demotes back to ``draft`` — except ``sent_confirmed``, which is terminal and
rejects edits. Suppression (do_not_contact) blocks all outbound steps. The
app never sends anything — ``open`` returns a ``wa.me`` URL for the user to
send manually, and ``sent_confirmed`` is written only by an explicit
confirmation (P2/P3/P4).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ai import get_generator
from app.models import Finding, FollowUp, OutreachMessage, Prospect
from app.services.activity import log_activity
from app.services.prospect import transition_stage
from app.whatsapp import build_wa_me_url


def _now() -> datetime:
    return datetime.now(UTC)


def _prospect(db: Session, prospect_id: int) -> Prospect | None:
    return db.get(Prospect, prospect_id)


def _is_suppressed(db: Session, prospect_id: int) -> bool:
    prospect = db.get(Prospect, prospect_id)
    return bool(prospect is not None and prospect.do_not_contact)


def _grounding_ok(message: OutreachMessage) -> bool:
    return bool(message.grounding)


def create_draft(
    db: Session,
    *,
    prospect_id: int,
    current_text: str,
    origin: str = "template",
    generated_text: str | None = None,
    grounding: list[int] | None = None,
    ungrounded_acknowledgement: bool = False,
    actor: str = "user",
    allow_empty: bool = False,
) -> tuple[OutreachMessage | None, str | None]:
    """Create the active draft for a prospect (one active at a time).

    Blocked for suppressed prospects (Flow 8). Any previously active message
    is deactivated so only one draft is ever active. Set ``allow_empty`` to
    create a blank draft the user will fill in (used by "start new outreach").
    """
    if _is_suppressed(db, prospect_id):
        return None, "suppressed: cannot draft for a do-not-contact prospect"
    if not allow_empty and (not current_text or not current_text.strip()):
        return None, "Draft text is required"

    prospect = _prospect(db, prospect_id)
    if prospect is None:
        return None, "Prospect not found"
    if prospect.stage == "not_interested":
        # Flow 6.3: not_interested is terminal for outreach.
        return None, "not_interested: outreach is terminal for this prospect"

    # Grounding may only reference findings that belong to this prospect.
    if grounding:
        valid_ids = {
            f.id
            for f in db.query(Finding).filter(
                Finding.id.in_(grounding), Finding.prospect_id == prospect_id
            )
        }
        grounding = [g for g in grounding if g in valid_ids]

    # One active message per prospect.
    db.query(OutreachMessage).filter_by(prospect_id=prospect_id, active=True).update(
        {"active": False}
    )

    message = OutreachMessage(
        prospect_id=prospect_id,
        status="draft",
        active=True,
        current_text=current_text.strip(),
        generated_text=generated_text,
        origin=origin,
        grounding=grounding,
        ungrounded_acknowledgement=ungrounded_acknowledgement,
    )
    db.add(message)
    db.flush()

    log_activity(
        db,
        prospect_id=prospect_id,
        type="draft_created",
        actor=actor,
        payload={
            "message_id": message.id,
            "origin": origin,
            "grounding": grounding,
        },
    )
    return message, None


def generate_draft(
    db: Session,
    *,
    prospect_id: int,
    actor: str = "user",
) -> tuple[OutreachMessage | None, str | None]:
    """Generate a draft from research findings using the configured generator.

    Blocked for suppressed prospects. Grounds the draft in validated
    opportunities + verified/high-confidence findings (P5: AI findings start
    unverified and are labelled origin='ai').
    """
    if _is_suppressed(db, prospect_id):
        return None, "suppressed: cannot draft for a do-not-contact prospect"
    prospect = _prospect(db, prospect_id)
    if prospect is None:
        return None, "Prospect not found"

    # Gather context: validated opportunities + usable findings.
    from app.models import Opportunity

    opportunities = (
        db.query(Opportunity).filter_by(prospect_id=prospect_id, status="validated").all()
    )
    findings = (
        db.query(Finding)
        .filter_by(prospect_id=prospect_id)
        .order_by(Finding.verification_status.desc(), Finding.confidence.desc())
        .all()
    )

    ctx = _build_context(prospect, opportunities, findings)
    generator = get_generator()
    draft = asyncio.run(generator.generate(ctx))

    return create_draft(
        db,
        prospect_id=prospect_id,
        current_text=draft.text,
        origin=draft.origin,
        generated_text=draft.text,
        grounding=draft.grounding,
        actor=actor,
    )


def _build_context(
    prospect: Prospect,
    opportunities: list[Any],
    findings: list[Finding],
) -> Any:
    from app.ai.generator import OutreachContext

    return OutreachContext(
        prospect_name=prospect.business_name,
        phone_e164=prospect.phone_e164,
        opportunities=[
            {"type": o.type, "summary": o.summary, "rationale": o.rationale or ""}
            for o in opportunities
        ],
        findings=[
            {"id": f.id, "statement": f.statement, "source_url": f.source_url}
            for f in findings
            if f.verification_status in ("verified", "unverified")
        ][:5],
    )


def submit_message(
    db: Session,
    *,
    message_id: int,
    current_text: str,
    actor: str = "user",
) -> tuple[OutreachMessage | None, str | None]:
    """Submit a draft for review: draft → pending_review (guard: non-empty)."""
    message = db.get(OutreachMessage, message_id)
    if message is None:
        return None, "Message not found"
    if message.status != "draft":
        return None, f"Cannot submit a message in status {message.status}"
    if not current_text or not current_text.strip():
        return None, "Message text is required"

    message.current_text = current_text.strip()
    message.status = "pending_review"
    db.flush()

    log_activity(
        db,
        prospect_id=message.prospect_id,
        type="draft_submitted",
        actor=actor,
        payload={"message_id": message_id},
    )
    return message, None


def approve_message(
    db: Session,
    *,
    message_id: int,
    ungrounded_acknowledgement: bool = False,
    actor: str = "user",
) -> tuple[OutreachMessage | None, str | None]:
    """Approve for sending: pending_review → approved.

    Blocked if: prospect suppressed, empty text, or no grounding without the
    "I accept ungrounded content" acknowledgement (Flow 5, P5).
    """
    message = db.get(OutreachMessage, message_id)
    if message is None:
        return None, "Message not found"
    if message.status != "pending_review":
        return None, f"Cannot approve a message in status {message.status}"
    if _is_suppressed(db, message.prospect_id):
        return None, "suppressed: cannot approve outreach for a do-not-contact prospect"
    if not message.current_text or not message.current_text.strip():
        return None, "Message text is required"
    if not _grounding_ok(message) and not ungrounded_acknowledgement:
        return None, (
            "Message needs at least one grounded finding, or acknowledge "
            "you accept ungrounded content"
        )

    message.status = "approved"
    message.ungrounded_acknowledgement = ungrounded_acknowledgement
    message.approved_at = _now()
    db.flush()

    log_activity(
        db,
        prospect_id=message.prospect_id,
        type="draft_approved",
        actor=actor,
        payload={"message_id": message_id},
    )
    return message, None


def open_message(
    db: Session,
    *,
    message_id: int,
    actor: str = "user",
) -> tuple[OutreachMessage | None, str | None, str | None]:
    """Open in WhatsApp: approved → opened_in_whatsapp.

    Returns (message, error, wa_me_url). Builds the wa.me URL but never sends
    — the user presses Send in WhatsApp (P4).
    """
    message = db.get(OutreachMessage, message_id)
    if message is None:
        return None, "Message not found", None
    if message.status != "approved":
        return None, f"Cannot open a message in status {message.status}", None
    if _is_suppressed(db, message.prospect_id):
        return None, "suppressed: cannot open WhatsApp for a do-not-contact prospect", None

    prospect = _prospect(db, message.prospect_id)
    if prospect is None or not prospect.phone_e164:
        return (
            None,
            "Prospect has no normalized phone number; add one before opening WhatsApp",
            None,
        )

    wa_me_url = build_wa_me_url(prospect.phone_e164, message.current_text)
    message.status = "opened_in_whatsapp"
    message.opened_at = _now()
    db.flush()

    log_activity(
        db,
        prospect_id=message.prospect_id,
        type="whatsapp_opened",
        actor=actor,
        payload={"message_id": message_id, "wa_me_url": wa_me_url},
    )
    return message, None, wa_me_url


def confirm_sent(
    db: Session,
    *,
    message_id: int,
    actor: str = "user",
) -> tuple[OutreachMessage | None, str | None]:
    """Confirm the user sent the message: opened_in_whatsapp → sent_confirmed.

    The sole writer of sent_confirmed (P2). Side effects: activity entry,
    prospect.last_contacted_at, stage advanced to "contacted".
    """
    message = db.get(OutreachMessage, message_id)
    if message is None:
        return None, "Message not found"
    if message.status != "opened_in_whatsapp":
        return None, (
            f"Cannot confirm a message in status {message.status}; open it in WhatsApp first"
        )

    message.status = "sent_confirmed"
    message.sent_confirmed_at = _now()
    db.flush()

    prospect = _prospect(db, message.prospect_id)
    if prospect is None:
        return None, "Prospect not found"
    prospect.last_contacted_at = _now()
    # Advance the stage unless the prospect already sits at/after contacted
    # or in a state that must not be overwritten (terminal dispositions,
    # archived) or follow_up_due (still needs its follow-up — a re-contact
    # must not silently clear it). USER_FLOWS Flow 5.8 promises contacted;
    # the inbound edge exists from every active pre-contact stage.
    if prospect.stage not in {
        "contacted",
        "replied",
        "interested",
        "converted",
        "follow_up_due",
        "not_interested",
        "do_not_contact",
        "archived",
    }:
        transition_stage(db, prospect_id=message.prospect_id, new_stage="contacted", actor=actor)

    log_activity(
        db,
        prospect_id=message.prospect_id,
        type="message_sent_confirmed",
        actor=actor,
        payload={
            "message_id": message_id,
            "last_contacted_at": prospect.last_contacted_at.isoformat(),
        },
    )
    return message, None


def not_sent(
    db: Session,
    *,
    message_id: int,
    actor: str = "user",
) -> tuple[OutreachMessage | None, str | None]:
    """User did not send: opened_in_whatsapp → approved (ready to re-open)."""
    message = db.get(OutreachMessage, message_id)
    if message is None:
        return None, "Message not found"
    if message.status != "opened_in_whatsapp":
        return None, f"Cannot reset a message in status {message.status}"
    message.status = "approved"
    message.opened_at = None
    db.flush()

    log_activity(
        db,
        prospect_id=message.prospect_id,
        type="message_not_sent",
        actor=actor,
        payload={"message_id": message_id, "reset_from": "opened_in_whatsapp"},
    )
    return message, None


def edit_message(
    db: Session,
    *,
    message_id: int,
    current_text: str,
    actor: str = "user",
) -> tuple[OutreachMessage | None, str | None]:
    """Edit text: any status → draft (edits invalidate approval, §3.1).

    ``sent_confirmed`` is terminal (§3.1) — editing it would silently erase
    the send confirmation and rewrite history, so it is rejected instead.
    """
    message = db.get(OutreachMessage, message_id)
    if message is None:
        return None, "Message not found"
    if message.status == "sent_confirmed":
        return None, (
            "Cannot edit a message that was confirmed sent; start a new outreach draft instead"
        )
    if not current_text or not current_text.strip():
        return None, "Message text is required"

    message.current_text = current_text.strip()
    message.status = "draft"
    message.approved_at = None
    message.opened_at = None
    message.sent_confirmed_at = None
    db.flush()

    log_activity(
        db,
        prospect_id=message.prospect_id,
        type="draft_edited",
        actor=actor,
        payload={"message_id": message_id},
    )
    return message, None


def record_reply(
    db: Session,
    *,
    prospect_id: int,
    text: str,
    received_at: datetime | None = None,
    actor: str = "user",
) -> tuple[Prospect | None, str | None]:
    """Record an inbound WhatsApp reply: activity + stage → replied (Flow 6)."""
    prospect = _prospect(db, prospect_id)
    if prospect is None:
        return None, "Prospect not found"
    if _is_suppressed(db, prospect_id):
        return None, "suppressed: cannot record a reply for a do-not-contact prospect"

    log_activity(
        db,
        prospect_id=prospect_id,
        type="reply_received",
        actor=actor,
        payload={"text": text, "received_at": (received_at or _now()).isoformat()},
    )

    # Advance unless already at/after replied or in a state a reply must not
    # overwrite (USER_FLOWS Flow 6.2 promises stage → replied; the inbound
    # edge exists from every active stage).
    if prospect.stage not in {
        "replied",
        "interested",
        "converted",
        "not_interested",
        "do_not_contact",
        "archived",
    }:
        transition_stage(db, prospect_id=prospect_id, new_stage="replied", actor=actor)
    return prospect, None


def start_new_outreach(
    db: Session,
    *,
    prospect_id: int,
    actor: str = "user",
) -> tuple[OutreachMessage | None, str | None]:
    """Start a new draft after a message is sent_confirmed (Flow 7 guard).

    Blocked for do_not_contact. The previous sent_confirmed message stays as
    history; the new draft becomes the single active one.
    """
    if _is_suppressed(db, prospect_id):
        return None, "suppressed: cannot start outreach for a do-not-contact prospect"
    return create_draft(db, prospect_id=prospect_id, current_text="", actor=actor, allow_empty=True)


def create_follow_up(
    db: Session,
    *,
    prospect_id: int,
    due_date: datetime | None,
    note: str | None = None,
    actor: str = "user",
) -> tuple[FollowUp | None, str | None]:
    """Create an open follow-up task for a prospect (Flow 7)."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"
    follow_up = FollowUp(
        prospect_id=prospect_id,
        due_date=due_date,
        note=note,
        status="open",
    )
    db.add(follow_up)
    db.flush()
    log_activity(
        db,
        prospect_id=prospect_id,
        type="follow_up_set",
        actor=actor,
        payload={"follow_up_id": follow_up.id, "due_date": str(due_date) if due_date else None},
    )
    return follow_up, None
