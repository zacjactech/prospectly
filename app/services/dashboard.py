"""Dashboard read-model: pipeline stage counts (SYSTEM_ARCHITECTURE §2).

Counts only — no metric definitions beyond what the roadmap scopes to Phase 4
(see docs/UI_UX_SPECIFICATION.md §2.1: the dashboard renders a pipeline strip
now; "needs attention" and "today" queues arrive in Phase 4).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Prospect

# The main progression (SYSTEM_ARCHITECTURE §3.2), in order. Terminal states
# (not_interested, do_not_contact, archived) are deliberately absent: they are
# closed, not in-flight.
PIPELINE_STAGES: tuple[str, ...] = (
    "new",
    "researched",
    "opportunity_identified",
    "outreach_ready",
    "contacted",
    "replied",
    "follow_up_due",
    "interested",
    "converted",
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
