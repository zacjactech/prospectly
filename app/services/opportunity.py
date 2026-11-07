"""Opportunity rule engine (Flow 4, DATABASE_DESIGN §2.8).

Proposes evidence-backed outreach reasons from a prospect's research findings.
Proposing happens automatically after research completes; validating or
dismissing is an explicit operator decision. AI-only evidence never
auto-validates an opportunity (§2.8 guard).

The engine is presentation-neutral: it only writes `opportunities` +
`opportunity_findings` rows and logs activities. Routes + templates expose
them to the user.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Finding, Opportunity, OpportunityFinding, Prospect
from app.services.activity import log_activity
from app.services.prospect import transition_stage

_EVIDENCE_STATUSES = ("verified", "unverified")

_TYPE_META: dict[str, dict[str, str]] = {
    "no_standalone_website": {
        "summary": "No standalone website found",
        "rationale": "Searches and DNS checks found no site for this business, "
        "so there is no online inquiry path to work with.",
    },
    "website_inaccessible": {
        "summary": "Website could not be reached",
        "rationale": "The site exists on paper but inspection failed, which "
        "hurts credibility and inbound leads.",
    },
    "no_inquiry_path": {
        "summary": "No contact or inquiry path on the website",
        "rationale": "The site is reachable but has no form, email, phone, "
        "or booking link for visitors to use.",
    },
    "unclear_services": {
        "summary": "Services not clearly documented online",
        "rationale": "The business exists but its offer/services are not "
        "described, so prospects cannot tell what it does.",
    },
}


def _evidence_statuses_ok(findings: list[Finding]) -> bool:
    """§2.8 guard: at least one linked finding is verified or unverified."""
    return any(f.verification_status in _EVIDENCE_STATUSES for f in findings)


def propose_opportunities(
    db: Session,
    *,
    prospect_id: int,
    research_run_id: int | None = None,
) -> list[Opportunity]:
    """Propose opportunities from research findings; idempotent by type.

    Returns every opportunity in ``proposed`` state for the prospect after
    this call (newly created ones plus any already proposed). Never raises;
    failures are swallowed so research is never blocked by the rule engine.
    """
    try:
        return _propose_opportunities(db, prospect_id=prospect_id, research_run_id=research_run_id)
    except Exception:  # pragma: no cover - defensive, research must not fail
        return []


def _propose_opportunities(
    db: Session,
    *,
    prospect_id: int,
    research_run_id: int | None = None,
) -> list[Opportunity]:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return []

    findings = db.query(Finding).filter_by(prospect_id=prospect_id).all()
    findings_by_kind: dict[str, list[Finding]] = {}
    for finding in findings:
        findings_by_kind.setdefault(finding.kind, []).append(finding)

    website_status = (prospect.website_status or "unknown").strip()

    existing_types = {
        o.type for o in db.query(Opportunity).filter_by(prospect_id=prospect_id).all()
    }

    # Each rule: (type, trigger, evidence). A rule only fires once per type.
    rules: list[tuple[str, Any, Any]] = [
        (
            "no_standalone_website",
            lambda status, _kinds: status == "no_website_found",
            lambda kinds: kinds.get("website", []),
        ),
        (
            "website_inaccessible",
            lambda status, _kinds: status == "check_failed",
            lambda kinds: kinds.get("website", []),
        ),
        (
            "no_inquiry_path",
            lambda status, kinds: status == "reachable" and "contact" not in kinds,
            lambda kinds: kinds.get("website", []),
        ),
        (
            "unclear_services",
            lambda status, kinds: (
                status == "reachable"
                and "contact" not in kinds
                and "website_quality" not in kinds
                and "identity" in kinds
            ),
            lambda kinds: kinds.get("identity", []) + kinds.get("website", []),
        ),
    ]

    for otype, trigger, evidence in rules:
        if otype in existing_types:
            continue  # already proposed / validated / dismissed; no duplicates
        if not trigger(website_status, findings_by_kind):
            continue
        ev = evidence(findings_by_kind)
        if not ev or not _evidence_statuses_ok(ev):
            continue  # §2.8 guard: needs evidence we can stand behind

        opportunity = Opportunity(
            prospect_id=prospect_id,
            type=otype,
            summary=_TYPE_META[otype]["summary"],
            rationale=_TYPE_META[otype]["rationale"],
            status="proposed",
            origin="rule",
        )
        db.add(opportunity)
        db.flush()
        for finding in ev:
            db.add(OpportunityFinding(opportunity_id=opportunity.id, finding_id=finding.id))
        log_activity(
            db,
            prospect_id=prospect_id,
            type="opportunity_created",
            actor="system",
            payload={"opportunity_id": opportunity.id, "type": otype},
        )

    return db.query(Opportunity).filter_by(prospect_id=prospect_id, status="proposed").all()


def validate_opportunity(
    db: Session,
    *,
    opportunity_id: int,
    actor: str = "user",
) -> tuple[Opportunity | None, str | None]:
    """Validate a proposed opportunity (operator decision, never automatic).

    Moves the prospect to ``opportunity_identified`` when it is still at
    ``researched``. Requires ≥1 evidence-linked verified/unverified finding
    (§2.8 guard). AI-only evidence cannot be validated.
    """
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None:
        return None, "Opportunity not found"
    if opportunity.status != "proposed":
        return None, f"Cannot validate opportunity in status {opportunity.status}"

    linked = (
        db.query(Finding)
        .join(
            OpportunityFinding,
            OpportunityFinding.finding_id == Finding.id,
        )
        .filter(OpportunityFinding.opportunity_id == opportunity_id)
        .all()
    )
    evidence = [f for f in linked if f.verification_status in _EVIDENCE_STATUSES]
    if not evidence:
        return None, (
            "Opportunity cannot be validated without evidence-linked findings; "
            "add or verify a finding first"
        )
    # P5 + §2.8: never auto-validate on AI-only evidence. At least one
    # verified/unverified finding must be non-AI (human or provider).
    if all(f.origin == "ai" for f in evidence):
        return None, (
            "Opportunity cannot be validated on AI-only evidence; add or "
            "verify a human/provider finding first"
        )

    opportunity.status = "validated"
    opportunity.updated_at = datetime.now(UTC)
    db.flush()

    if opportunity.prospect.stage == "researched":
        transition_stage(
            db, opportunity.prospect_id, new_stage="opportunity_identified", actor=actor
        )

    log_activity(
        db,
        prospect_id=opportunity.prospect_id,
        type="opportunity_validated",
        actor=actor,
        payload={"opportunity_id": opportunity_id},
    )
    return opportunity, None


def dismiss_opportunity(
    db: Session,
    *,
    opportunity_id: int,
    reason: str | None = None,
    actor: str = "user",
) -> tuple[Opportunity | None, str | None]:
    """Dismiss a proposed opportunity (operator decision)."""
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None:
        return None, "Opportunity not found"
    if opportunity.status != "proposed":
        return None, f"Cannot dismiss opportunity in status {opportunity.status}"

    opportunity.status = "dismissed"
    opportunity.updated_at = datetime.now(UTC)
    db.flush()

    log_activity(
        db,
        prospect_id=opportunity.prospect_id,
        type="opportunity_dismissed",
        actor=actor,
        payload={"opportunity_id": opportunity_id, "reason": reason},
    )
    return opportunity, None


def list_opportunities(db: Session, prospect_id: int) -> list[Opportunity]:
    """All opportunities for a prospect, newest first."""
    return (
        db.query(Opportunity)
        .filter_by(prospect_id=prospect_id)
        .order_by(Opportunity.created_at.desc())
        .all()
    )


def get_opportunity(db: Session, opportunity_id: int) -> Opportunity | None:
    return db.get(Opportunity, opportunity_id)
