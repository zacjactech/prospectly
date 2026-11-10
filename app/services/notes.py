"""Notes service — add/edit free-form notes on a prospect (DATABASE_DESIGN §2.4).

USER_FLOWS Flow 9 records the conversion handoff as a note; UI spec §2.3 lists
"notes (add/edit)" on the prospect overview. Notes are editable history, not
evidence, so there is no verification workflow here. Both operations append an
activity row (``note_added`` / ``note_edited``).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Note, Prospect
from app.services.activity import log_activity

MAX_BODY = 2000
PAYLOAD_PREVIEW = 200


def add_note(
    db: Session,
    *,
    prospect_id: int,
    body: str,
    actor: str = "user",
) -> tuple[Note | None, str | None]:
    """Create a note; body is stripped, non-empty and ≤2000 characters."""
    body = (body or "").strip()
    if not body:
        return None, "Note body is required"
    if len(body) > MAX_BODY:
        return None, f"Note body must be {MAX_BODY} characters or fewer"

    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"

    note = Note(prospect_id=prospect_id, body=body)
    db.add(note)
    db.flush()

    log_activity(
        db,
        prospect_id=prospect_id,
        type="note_added",
        actor=actor,
        payload={"note_id": note.id},
    )
    return note, None


def edit_note(
    db: Session,
    *,
    note_id: int,
    body: str,
    actor: str = "user",
) -> tuple[Note | None, str | None]:
    """Edit a note's body; validation matches :func:`add_note`."""
    note = db.get(Note, note_id)
    if note is None:
        return None, "Note not found"

    body = (body or "").strip()
    if not body:
        return None, "Note body is required"
    if len(body) > MAX_BODY:
        return None, f"Note body must be {MAX_BODY} characters or fewer"

    previous = note.body
    note.body = body
    db.flush()

    log_activity(
        db,
        prospect_id=note.prospect_id,
        type="note_edited",
        actor=actor,
        payload={
            "note_id": note.id,
            "from": previous[:PAYLOAD_PREVIEW],
            "to": body[:PAYLOAD_PREVIEW],
        },
    )
    return note, None
