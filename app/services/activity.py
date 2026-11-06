"""Activity logging service (append-only history, DATABASE_DESIGN §2.11)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Activity


def log_activity(
    db: Session,
    *,
    prospect_id: int,
    type: str,
    actor: str = "user",
    payload: dict | None = None,
) -> Activity:
    """Append an activity row. Never updated or deleted after creation."""
    activity = Activity(
        prospect_id=prospect_id,
        type=type,
        actor=actor,
        payload=payload,
    )
    db.add(activity)
    db.flush()
    return activity
