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
    AIUsage,
    Finding,
    FollowUp,
    OutreachMessage,
    Prospect,
    ProviderUsage,
    ResearchRun,
)
from app.services import authz

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


def stage_counts(db: Session, *, visible_to: authz.Identity | None = None) -> dict[str, int]:
    """Non-archived prospect count per pipeline stage, zero-filled.

    Partial index on `stage` serves this query (DATABASE_DESIGN §9).
    ``visible_to`` scopes the counts to the prospects that user may see.
    """
    stmt = select(Prospect.stage, func.count(Prospect.id)).where(Prospect.archived_at.is_(None))
    scope = authz.scope_criterion(visible_to)
    if scope is not None:
        stmt = stmt.where(scope)
    rows = db.execute(stmt.group_by(Prospect.stage)).all()
    counts = dict(rows)
    return {stage: int(counts.get(stage, 0)) for stage in PIPELINE_STAGES}


def total_prospects(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Count of non-archived prospects (PRODUCT_BRIEF §8 'Total prospects')."""
    stmt = select(func.count(Prospect.id)).where(Prospect.archived_at.is_(None))
    scope = authz.scope_criterion(visible_to)
    if scope is not None:
        stmt = stmt.where(scope)
    return int(db.execute(stmt).scalar_one())


# --- PRODUCT_BRIEF §8 operational metrics ---


def researched_prospects(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Prospects with ≥1 completed research run (§8 'Researched')."""
    stmt = select(ResearchRun.prospect_id).where(ResearchRun.status == "succeeded").distinct()
    ids = authz.scoped_prospect_subq(visible_to)
    if ids is not None:
        stmt = stmt.where(ResearchRun.prospect_id.in_(ids))
    return int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())


def requires_review_prospects(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Prospects with unverified/contradicted findings or pending drafts (§8)."""
    ids = authz.scoped_prospect_subq(visible_to)
    unverified = select(Finding.prospect_id).where(
        Finding.verification_status.in_(("unverified", "contradicted"))
    )
    pending = select(OutreachMessage.prospect_id).where(OutreachMessage.status == "pending_review")
    if ids is not None:
        unverified = unverified.where(Finding.prospect_id.in_(ids))
        pending = pending.where(OutreachMessage.prospect_id.in_(ids))
    combined = unverified.union_all(pending).subquery()
    return int(
        db.execute(
            select(func.count(func.distinct(combined.c.prospect_id))).select_from(combined)
        ).scalar_one()
    )


def approved_messages(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Drafts in `approved` (not yet opened) state (§8 'Approved messages')."""
    stmt = select(func.count(OutreachMessage.id)).where(OutreachMessage.status == "approved")
    ids = authz.scoped_prospect_subq(visible_to)
    if ids is not None:
        stmt = stmt.where(OutreachMessage.prospect_id.in_(ids))
    return int(db.execute(stmt).scalar_one())


def sent_confirmed_messages(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Messages in `sent_confirmed` (§8 'Sent (confirmed)')."""
    stmt = select(func.count(OutreachMessage.id)).where(OutreachMessage.status == "sent_confirmed")
    ids = authz.scoped_prospect_subq(visible_to)
    if ids is not None:
        stmt = stmt.where(OutreachMessage.prospect_id.in_(ids))
    return int(db.execute(stmt).scalar_one())


def replied_prospects(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Prospects with ≥1 inbound reply activity (§8 'Replies')."""
    stmt = select(Activity.prospect_id).where(Activity.type == "reply_received").distinct()
    ids = authz.scoped_prospect_subq(visible_to)
    if ids is not None:
        stmt = stmt.where(Activity.prospect_id.in_(ids))
    return int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())


def interested_prospects(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Prospects in `interested` stage (§8 'Interested')."""
    stmt = (
        select(func.count(Prospect.id))
        .where(Prospect.archived_at.is_(None))
        .where(Prospect.stage == "interested")
    )
    scope = authz.scope_criterion(visible_to)
    if scope is not None:
        stmt = stmt.where(scope)
    return int(db.execute(stmt).scalar_one())


def follow_ups_due(
    db: Session, today: date | None = None, *, visible_to: authz.Identity | None = None
) -> int:
    """Open follow-ups with `due_date ≤ today` (§8 'Follow-ups due')."""
    today = today or date.today()
    end_of_today = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=UTC)
    stmt = (
        select(func.count(FollowUp.id))
        .where(FollowUp.status == "open")
        .where(FollowUp.due_date.is_not(None))
        .where(FollowUp.due_date <= end_of_today)
    )
    ids = authz.scoped_prospect_subq(visible_to)
    if ids is not None:
        stmt = stmt.where(FollowUp.prospect_id.in_(ids))
    return int(db.execute(stmt).scalar_one())


def follow_ups_overdue(
    db: Session, today: date | None = None, *, visible_to: authz.Identity | None = None
) -> int:
    """Open follow-ups with `due_date < today` (start of today, UTC)."""
    today = today or date.today()
    start_of_today = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=UTC)
    stmt = (
        select(func.count(FollowUp.id))
        .where(FollowUp.status == "open")
        .where(FollowUp.due_date.is_not(None))
        .where(FollowUp.due_date < start_of_today)
    )
    ids = authz.scoped_prospect_subq(visible_to)
    if ids is not None:
        stmt = stmt.where(FollowUp.prospect_id.in_(ids))
    return int(db.execute(stmt).scalar_one())


def converted_prospects(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Prospects in `converted` stage (§8 'Converted')."""
    stmt = (
        select(func.count(Prospect.id))
        .where(Prospect.archived_at.is_(None))
        .where(Prospect.stage == "converted")
    )
    scope = authz.scope_criterion(visible_to)
    if scope is not None:
        stmt = stmt.where(scope)
    return int(db.execute(stmt).scalar_one())


def dashboard_metrics(db: Session, *, visible_to: authz.Identity | None = None) -> dict[str, int]:
    """All PRODUCT_BRIEF §8 operational metrics in one dict."""
    return {
        "total_prospects": total_prospects(db, visible_to=visible_to),
        "researched": researched_prospects(db, visible_to=visible_to),
        "requires_review": requires_review_prospects(db, visible_to=visible_to),
        "approved_messages": approved_messages(db, visible_to=visible_to),
        "sent_confirmed": sent_confirmed_messages(db, visible_to=visible_to),
        "replies": replied_prospects(db, visible_to=visible_to),
        "interested": interested_prospects(db, visible_to=visible_to),
        "follow_ups_due": follow_ups_due(db, visible_to=visible_to),
        "converted": converted_prospects(db, visible_to=visible_to),
    }


# --- "Needs attention" queue (UI/UX spec §2.1) ---


def pending_review_messages(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Drafts awaiting approval (§2.1 'drafts pending review')."""
    stmt = select(func.count(OutreachMessage.id)).where(OutreachMessage.status == "pending_review")
    ids = authz.scoped_prospect_subq(visible_to)
    if ids is not None:
        stmt = stmt.where(OutreachMessage.prospect_id.in_(ids))
    return int(db.execute(stmt).scalar_one())


def research_failed_prospects(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Prospects whose latest research run failed (§2.1 'research runs failed')."""
    stmt = select(ResearchRun.prospect_id).where(ResearchRun.status == "failed").distinct()
    ids = authz.scoped_prospect_subq(visible_to)
    if ids is not None:
        stmt = stmt.where(ResearchRun.prospect_id.in_(ids))
    return int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())


def findings_unverified_prospects(db: Session, *, visible_to: authz.Identity | None = None) -> int:
    """Prospects with ≥1 unverified finding (§2.1 'findings unverified')."""
    stmt = select(Finding.prospect_id).where(Finding.verification_status == "unverified").distinct()
    ids = authz.scoped_prospect_subq(visible_to)
    if ids is not None:
        stmt = stmt.where(Finding.prospect_id.in_(ids))
    return int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())


def needs_attention(
    db: Session, today: date | None = None, *, visible_to: authz.Identity | None = None
) -> list[dict[str, Any]]:
    """Items needing the operator's attention, in urgency order (§2.1).

    Buckets: overdue follow-ups → drafts pending review → failed research runs
    → unverified findings. Empty list means "nothing needs attention".
    """
    today = today or date.today()
    items: list[dict[str, Any]] = []

    overdue = follow_ups_overdue(db, today, visible_to=visible_to)
    if overdue:
        items.append(
            {
                "kind": "follow_up_overdue",
                "label": "Overdue follow-ups",
                "count": overdue,
                "href": "/prospects?stage=follow_up_due",
            }
        )

    pending = pending_review_messages(db, visible_to=visible_to)
    if pending:
        items.append(
            {
                "kind": "pending_review",
                "label": "Drafts pending review",
                "count": pending,
                "href": "/outreach",
            }
        )

    failed = research_failed_prospects(db, visible_to=visible_to)
    if failed:
        items.append(
            {
                "kind": "research_failed",
                "label": "Research runs failed",
                "count": failed,
                "href": "/prospects?stage=researched",
            }
        )

    unverified = findings_unverified_prospects(db, visible_to=visible_to)
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


# --- Usage reporting (UI/UX spec §2.7) ---


def usage_stats(db: Session) -> dict[str, Any]:
    """Provider + AI call accounting per day (Settings → Usage page).

    Groups provider_usage and ai_usage by provider/period for a simple table,
    no charts.
    """
    provider_rows = (
        db.query(ProviderUsage)
        .order_by(ProviderUsage.period.desc(), ProviderUsage.provider, ProviderUsage.kind)
        .all()
    )
    ai_rows = (
        db.query(AIUsage)
        .order_by(AIUsage.period.desc(), AIUsage.provider, AIUsage.model, AIUsage.task)
        .all()
    )

    providers: dict[str, list[dict[str, Any]]] = {}
    for prow in provider_rows:
        providers.setdefault(prow.provider, []).append(
            {
                "kind": prow.kind,
                "period": prow.period.isoformat(),
                "calls": prow.calls,
                "errors": prow.errors,
                "rate_limited": prow.rate_limited,
            }
        )

    ai: dict[str, list[dict[str, Any]]] = {}
    for arow in ai_rows:
        key = f"{arow.provider} / {arow.model or 'default'}"
        ai.setdefault(key, []).append(
            {
                "task": arow.task,
                "period": arow.period.isoformat(),
                "calls": arow.calls,
                "ok_calls": arow.ok_calls,
                "failed_calls": arow.failed_calls,
                "prompt_tokens": arow.prompt_tokens,
                "completion_tokens": arow.completion_tokens,
            }
        )

    return {"providers": providers, "ai": ai}
