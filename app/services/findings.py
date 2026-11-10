"""Findings evidence CRUD — human add / verify / edit / delete (DATABASE_DESIGN §2.6).

The research pipeline writes evidence itself (``app/services/research.py``);
this service owns the operator-facing surface promised by USER_FLOWS P8,
UI spec §2.3 and API_DESIGN §2.3.

Invariants enforced here (P5/P7, §2.6):
- A manually added finding is forced to ``origin='human'``,
  ``verification_status='unverified'``, ``confidence='low'``,
  ``source_type='user'`` and ``research_run_id=None``. Clients cannot set
  origin/verification/confidence.
- ``(prospect_id, finding_key)`` stays unique — duplicate creates and
  statement-changing edits that collide return an error (the route maps it
  to 409) instead of letting the DB index surface as a 500.
- Verification may raise the status (a human action), and is idempotent.
- Edits keep ``origin`` unchanged, stamp ``edited_by`` and preserve the first
  ``original_statement`` of an ``origin='ai'`` finding.
- Deletion is a hard delete, blocked while the finding backs an opportunity
  (the FK would otherwise silently cascade the evidence links).
Every operation appends an activity row (append-only history, §2.11).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Finding, OpportunityFinding, Prospect
from app.services.activity import log_activity
from app.services.research import _finding_key

MAX_KIND = 40
MAX_STATEMENT = 500
DUPLICATE_ERROR = "identical finding already exists"
LINKED_ERROR = "linked to an opportunity — evidence must stand behind it"


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(value: str | None) -> str | None:
    """Strip an optional text field; an all-whitespace value becomes None."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def create_finding(
    db: Session,
    *,
    prospect_id: int,
    kind: str,
    statement: str,
    detail: str | None = None,
    source_url: str | None = None,
    actor: str = "user",
) -> tuple[Finding | None, str | None]:
    """Add a manually entered finding (P7). Forced human/unverified/low.

    Any ``origin``/``verification_status``/``confidence`` supplied by a client
    is ignored — this service has no parameters for them and always writes the
    human defaults.
    """
    kind = (kind or "").strip()
    statement = (statement or "").strip()
    if not kind:
        return None, "Kind is required"
    if len(kind) > MAX_KIND:
        return None, f"Kind must be {MAX_KIND} characters or fewer"
    if not statement:
        return None, "Statement is required"
    if len(statement) > MAX_STATEMENT:
        return None, f"Statement must be {MAX_STATEMENT} characters or fewer"

    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"

    key = _finding_key(kind, statement)
    existing = db.query(Finding).filter_by(prospect_id=prospect_id, finding_key=key).first()
    if existing is not None:
        return None, DUPLICATE_ERROR

    finding = Finding(
        prospect_id=prospect_id,
        research_run_id=None,
        kind=kind,
        statement=statement,
        detail=_clean(detail),
        source_url=_clean(source_url),
        source_type="user",
        retrieved_at=_now(),
        recorded_at=_now(),
        verification_status="unverified",
        confidence="low",
        origin="human",
        finding_key=key,
    )
    db.add(finding)
    db.flush()

    log_activity(
        db,
        prospect_id=prospect_id,
        type="finding_added",
        actor=actor,
        payload={"finding_id": finding.id, "kind": kind, "origin": "human"},
    )
    return finding, None


def verify_finding(
    db: Session,
    *,
    finding_id: int,
    actor: str = "user",
) -> tuple[Finding | None, str | None]:
    """Mark a finding verified (P5: only a human action raises the status).

    Idempotent: an already-verified finding returns success without a second
    activity row.
    """
    finding = db.get(Finding, finding_id)
    if finding is None:
        return None, "Finding not found"
    if finding.verification_status == "verified":
        return finding, None

    previous = finding.verification_status
    finding.verification_status = "verified"
    db.flush()

    log_activity(
        db,
        prospect_id=finding.prospect_id,
        type="finding_verified",
        actor=actor,
        payload={"finding_id": finding.id, "from": previous, "to": "verified"},
    )
    return finding, None


def edit_finding(
    db: Session,
    *,
    finding_id: int,
    statement: str,
    detail: str | None = None,
    actor: str = "user",
) -> tuple[Finding | None, str | None]:
    """Edit ``statement``/``detail`` only; preserve evidence provenance (§2.6).

    ``origin`` never changes. ``edited_by`` records the actor and, for an
    ``origin='ai'`` finding, the statement is preserved in
    ``original_statement`` on the first edit (a stored original is never
    overwritten). A statement change re-derives ``finding_key``; a collision
    with another finding of the same prospect is rejected.
    """
    finding = db.get(Finding, finding_id)
    if finding is None:
        return None, "Finding not found"

    statement = (statement or "").strip()
    if not statement:
        return None, "Statement is required"
    if len(statement) > MAX_STATEMENT:
        return None, f"Statement must be {MAX_STATEMENT} characters or fewer"

    old_statement = finding.statement
    statement_changed = statement != old_statement
    new_key = finding.finding_key
    if statement_changed:
        new_key = _finding_key(finding.kind, statement)
        collision = (
            db.query(Finding)
            .filter(
                Finding.prospect_id == finding.prospect_id,
                Finding.finding_key == new_key,
                Finding.id != finding.id,
            )
            .first()
        )
        if collision is not None:
            return None, DUPLICATE_ERROR

    # §2.6 audit: keep the AI original on the FIRST edit only.
    if finding.origin == "ai" and finding.original_statement is None:
        finding.original_statement = old_statement
    finding.edited_by = actor
    finding.statement = statement
    finding.detail = _clean(detail)
    finding.finding_key = new_key
    db.flush()

    log_activity(
        db,
        prospect_id=finding.prospect_id,
        type="finding_edited",
        actor=actor,
        payload={
            "finding_id": finding.id,
            "from_statement": old_statement,
            "to_statement": statement,
        },
    )
    return finding, None


def delete_finding(
    db: Session,
    *,
    finding_id: int,
    actor: str = "user",
) -> tuple[Finding | None, str | None]:
    """Hard-delete a finding unless an opportunity depends on it.

    ``opportunity_findings.finding_id`` has ON DELETE CASCADE, so allowing
    the delete would silently strip the evidence behind an opportunity. The
    operator must detach/dismiss the opportunity first.
    """
    finding = db.get(Finding, finding_id)
    if finding is None:
        return None, "Finding not found"

    linked = db.query(OpportunityFinding).filter_by(finding_id=finding_id).first()
    if linked is not None:
        return None, LINKED_ERROR

    prospect_id = finding.prospect_id
    payload = {
        "finding_id": finding.id,
        "statement": finding.statement,
        "origin": finding.origin,
    }
    db.delete(finding)
    db.flush()

    log_activity(
        db,
        prospect_id=prospect_id,
        type="finding_deleted",
        actor=actor,
        payload=payload,
    )
    return finding, None
