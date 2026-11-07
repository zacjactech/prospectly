"""Dashboard read-model: pipeline stage counts + operational metrics (Phase 4).

Pipeline strip counts per SYSTEM_ARCHITECTURE §2; operational metric
definitions per PRODUCT_BRIEF.md §8; "needs attention" queue per UI/UX spec §2.1.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Activity,
    Finding,
    FollowUp,
    OutreachMessage,
    Prospect,
    ResearchRun,
)

# The main progression (SYSTEM_ARCHITECTURE §3.2), in order. Terminal states
# (not_interested, do_not_contact, archived) are deliberately absent: they are
# closed, not in-flight.
#
# Grouped into display phases for the dashboard strip (UI/UX spec §2.1). The
# phases are presentation only: stage order, semantics and the state machine
# are unchanged, and PIPELINE_STAGES is derived so every existing caller sees
# the same flat tuple as before.
PIPELINE_PHASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Qualify", ("new", "researched", "opportunity_identified", "outreach_ready")),
    ("Outreach", ("contacted", "replied", "follow_up_due")),
    ("Convert", ("interested", "converted")),
)

PIPELINE_STAGES: tuple[str, ...] = tuple(
    stage for _phase, stages in PIPELINE_PHASES for stage in stages
)

_LABELS: dict[str, str] = {
    "new": "New",
    "researched": "Researched",
    "opportunity_identified": "Opportunity",
    "outreach_ready": "Ready",
    "contacted": "Contacted",
    "replied": "Replied",
    "follow_up_due": "Follow-up",
    "interested": "Interested",
    "converted": "Converted",
}


def stage_label(stage: str) -> str:
    """Display label for a pipeline stage."""
    return _LABELS.get(stage, stage.replace("_", " ").title())


def stage_counts(db: Session) -> dict[str, int]:
    """Non-archived prospect count per pipeline stage, zero-filled.

    Partial index on `stage` serves this query (DATABASE_DESIGN §9).
    """
    rows = db.execute(
        select(Prospect.stage, func.count(Prospect.id))
        .where(Prospect.archived_at.is_(None))
        .group_by(Prospect.stage)
    ).all()
    counts = dict(rows)
    return {stage: int(counts.get(stage, 0)) for stage in PIPELINE_STAGES}


def total_prospects(db: Session) -> int:
    """Count of non-archived prospects (PRODUCT_BRIEF §8 'Total prospects')."""
    return int(
        db.execute(
            select(func.count(Prospect.id)).where(Prospect.archived_at.is_(None))
        ).scalar_one()
    )


# --- PRODUCT_BRIEF §8 operational metrics ---


def researched_prospects(db: Session) -> int:
    """Prospects with ≥1 completed research run (§8 'Researched')."""
    subq = select(ResearchRun.prospect_id).where(ResearchRun.status == "succeeded").distinct()
    return int(db.execute(select(func.count()).select_from(subq.subquery())).scalar_one())


def requires_review_prospects(db: Session) -> int:
    """Prospects with unverified/contradicted findings or pending drafts (§8)."""
    unverified = select(Finding.prospect_id).where(
        Finding.verification_status.in_(("unverified", "contradicted"))
    )
    pending = select(OutreachMessage.prospect_id).where(OutreachMessage.status == "pending_review")
    combined = unverified.union_all(pending).subquery()
    return int(
        db.execute(
            select(func.count(func.distinct(combined.c.prospect_id))).select_from(combined)
        ).scalar_one()
    )


def approved_messages(db: Session) -> int:
    """Drafts in `approved` (not yet opened) state (§8 'Approved messages')."""
    return int(
        db.execute(
            select(func.count(OutreachMessage.id)).where(OutreachMessage.status == "approved")
        ).scalar_one()
    )


def sent_confirmed_messages(db: Session) -> int:
    """Messages in `sent_confirmed` (§8 'Sent (confirmed)')."""
    return int(
        db.execute(
            select(func.count(OutreachMessage.id)).where(OutreachMessage.status == "sent_confirmed")
        ).scalar_one()
    )


def replied_prospects(db: Session) -> int:
    """Prospects with ≥1 inbound reply activity (§8 'Replies')."""
    subq = select(Activity.prospect_id).where(Activity.type == "reply_received").distinct()
    return int(db.execute(select(func.count()).select_from(subq.subquery())).scalar_one())


def interested_prospects(db: Session) -> int:
    """Prospects in `interested` stage (§8 'Interested')."""
    return int(
        db.execute(
            select(func.count(Prospect.id))
            .where(Prospect.archived_at.is_(None))
            .where(Prospect.stage == "interested")
        ).scalar_one()
    )


def follow_ups_due(db: Session, today: date | None = None) -> int:
    """Open follow-ups with `due_date ≤ today` (§8 'Follow-ups due')."""
    today = today or date.today()
    end_of_today = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=UTC)
    return int(
        db.execute(
            select(func.count(FollowUp.id))
            .where(FollowUp.status == "open")
            .where(FollowUp.due_date.is_not(None))
            .where(FollowUp.due_date <= end_of_today)
        ).scalar_one()
    )


def follow_ups_overdue(db: Session, today: date | None = None) -> int:
    """Open follow-ups with `due_date < today` (start of today, UTC)."""
    today = today or date.today()
    start_of_today = datetime(today.year, today.month, today.day, tzinfo=UTC)
    return int(
        db.execute(
            select(func.count(FollowUp.id))
            .where(FollowUp.status == "open")
            .where(FollowUp.due_date.is_not(None))
            .where(FollowUp.due_date < start_of_today)
        ).scalar_one()
    )


def converted_prospects(db: Session) -> int:
    """Prospects in `converted` stage (§8 'Converted')."""
    return int(
        db.execute(
            select(func.count(Prospect.id))
            .where(Prospect.archived_at.is_(None))
            .where(Prospect.stage == "converted")
        ).scalar_one()
    )


def dashboard_metrics(db: Session) -> dict[str, int]:
    """All PRODUCT_BRIEF §8 operational metrics in one dict."""
    return {
        "total_prospects": total_prospects(db),
        "researched": researched_prospects(db),
        "requires_review": requires_review_prospects(db),
        "approved_messages": approved_messages(db),
        "sent_confirmed": sent_confirmed_messages(db),
        "replies": replied_prospects(db),
        "interested": interested_prospects(db),
        "follow_ups_due": follow_ups_due(db),
        "converted": converted_prospects(db),
    }


# --- "Needs attention" queue (UI/UX spec §2.1) ---


def pending_review_messages(db: Session) -> int:
    """Drafts awaiting approval (§2.1 'drafts pending review')."""
    return int(
        db.execute(
            select(func.count(OutreachMessage.id)).where(OutreachMessage.status == "pending_review")
        ).scalar_one()
    )


def research_failed_prospects(db: Session) -> int:
    """Prospects whose latest research run failed (§2.1 'research runs failed')."""
    subq = select(ResearchRun.prospect_id).where(ResearchRun.status == "failed").distinct()
    return int(db.execute(select(func.count()).select_from(subq.subquery())).scalar_one())


def findings_unverified_prospects(db: Session) -> int:
    """Prospects with ≥1 unverified finding (§2.1 'findings unverified')."""
    subq = select(Finding.prospect_id).where(Finding.verification_status == "unverified").distinct()
    return int(db.execute(select(func.count()).select_from(subq.subquery())).scalar_one())


def needs_attention(db: Session, today: date | None = None) -> list[dict[str, Any]]:
    """Items needing the operator's attention, in urgency order (§2.1).

    Buckets: overdue follow-ups → drafts pending review → failed research runs
    → unverified findings. Empty list means "nothing needs attention".
    """
    today = today or date.today()
    items: list[dict[str, Any]] = []

    overdue = follow_ups_overdue(db, today)
    if overdue:
        items.append(
            {
                "kind": "follow_up_overdue",
                "label": "Overdue follow-ups",
                "count": overdue,
                "href": "/prospects?stage=follow_up_due",
            }
        )

    pending = pending_review_messages(db)
    if pending:
        items.append(
            {
                "kind": "pending_review",
                "label": "Drafts pending review",
                "count": pending,
                "href": "/outreach",
            }
        )

    failed = research_failed_prospects(db)
    if failed:
        items.append(
            {
                "kind": "research_failed",
                "label": "Research runs failed",
                "count": failed,
                "href": "/prospects?stage=researched",
            }
        )

    unverified = findings_unverified_prospects(db)
    if unverified:
        items.append(
            {
                "kind": "findings_unverified",
                "label": "Findings unverified",
                "count": unverified,
                "href": "/prospects?stage=researched",
            }
        )

    return items
