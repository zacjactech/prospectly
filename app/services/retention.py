"""Retention sweep service (Phase 6+ free feature).

Flags prospects that have been inactive for more than N months
(default 12) for review/erasure. The operator sets the threshold and
can review flagged prospects before any action is taken.

Prospects are flagged with a "retention-review" tag and an activity
entry. No data is deleted automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import Prospect, Tag
from app.services.activity import log_activity


def _now() -> datetime:
    return datetime.now(UTC)


def _get_or_create_tag(db: Session, name: str, color: str | None = None) -> Tag:
    """Return the tag row, creating it if needed."""
    tag = db.query(Tag).filter(Tag.name == name).first()
    if tag is None:
        tag = Tag(name=name, color=color)
        db.add(tag)
        db.flush()
    return tag


def sweep_inactive_prospects(
    db: Session,
    *,
    months: int = 12,
    actor: str = "system",
) -> dict[str, Any]:
    """Flag prospects inactive for more than ``months`` months.

    A prospect is "inactive" if its ``updated_at`` is older than the cutoff
    and it is not already archived or do_not_contact.

    Returns a dict with:
      flagged: number of prospects newly flagged
      already_flagged: number already carrying the retention-review tag
      skipped: number excluded (archived, do_not_contact, or recently active)
      flagged_ids: list of newly flagged prospect IDs
    """
    cutoff = _now() - timedelta(days=30 * months)

    # Get the retention-review tag.
    tag = _get_or_create_tag(db, "retention-review", color="#b45309")

    # Candidates: live, not do_not_contact, updated before cutoff.
    candidates = (
        db.query(Prospect)
        .filter(Prospect.archived_at.is_(None))
        .filter(Prospect.do_not_contact.is_(False))
        .filter(Prospect.updated_at < cutoff)
        .all()
    )

    flagged = 0
    already_flagged = 0
    skipped = 0
    flagged_ids: list[int] = []

    for prospect in candidates:
        if tag in prospect.tags:
            already_flagged += 1
            continue

        prospect.tags.append(tag)
        log_activity(
            db,
            prospect_id=prospect.id,
            type="retention_review_flagged",
            actor=actor,
            payload={
                "months_inactive": months,
                "updated_at": prospect.updated_at.isoformat() if prospect.updated_at else None,
            },
        )
        flagged += 1
        flagged_ids.append(prospect.id)

    db.flush()

    return {
        "flagged": flagged,
        "already_flagged": already_flagged,
        "skipped": skipped,
        "flagged_ids": flagged_ids,
        "months": months,
        "cutoff": cutoff.isoformat(),
    }


def list_retention_flagged(
    db: Session,
) -> list[Prospect]:
    """Return all prospects carrying the retention-review tag."""
    tag = db.query(Tag).filter(Tag.name == "retention-review").first()
    if tag is None:
        return []
    return (
        db.query(Prospect)
        .join(Prospect.tags)
        .filter(Tag.id == tag.id)
        .filter(Prospect.archived_at.is_(None))
        .order_by(Prospect.updated_at.asc())
        .all()
    )


def clear_retention_flag(
    db: Session,
    prospect_id: int,
    *,
    actor: str = "user",
) -> bool:
    """Remove the retention-review tag from a prospect."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return False
    tag = db.query(Tag).filter(Tag.name == "retention-review").first()
    if tag is None or tag not in prospect.tags:
        return False
    prospect.tags.remove(tag)
    log_activity(
        db,
        prospect_id=prospect.id,
        type="retention_review_cleared",
        actor=actor,
    )
    db.flush()
    return True
