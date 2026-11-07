"""Merge service (Phase 6+ free feature).

Merges one prospect into another (target), re-linking all child rows
(notes, activities, findings, website_checks, opportunities, outreach
messages, follow-ups, tags) and logging the merge. The source prospect
is archived (not deleted) to preserve an audit trail.

DATABASE_DESIGN §2.2: "Merging is a manual action that re-links children
and logs the merge."
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    Prospect,
)
from app.services.activity import log_activity


def _now() -> datetime:
    return datetime.now(UTC)


def find_fuzzy_duplicates(
    db: Session,
    *,
    min_score: float = 0.85,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Find pairs of live prospects that are fuzzy duplicates.

    Returns a list of dicts with keys:
      source, target, score, reason

    ``source`` is the prospect that would be merged (archived).
    ``target`` is the prospect kept (merged into).
    The pair is ordered so source < target by id to avoid duplicates.
    """
    prospects = (
        db.query(Prospect).filter(Prospect.archived_at.is_(None)).order_by(Prospect.id.asc()).all()
    )

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int]] = set()

    for i, p1 in enumerate(prospects):
        for p2 in prospects[i + 1 :]:
            pair_key = (min(p1.id, p2.id), max(p1.id, p2.id))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            score = _fuzzy_score(
                p1.normalized_name or p1.business_name,
                p2.normalized_name or p2.business_name,
            )
            if score < min_score:
                continue

            # Determine reason for the match.
            reasons: list[str] = []
            if p1.phone_e164 and p2.phone_e164 and p1.phone_e164 == p2.phone_e164:
                reasons.append("same phone")
            if p1.email and p2.email and p1.email.lower() == p2.email.lower():
                reasons.append("same email")
            if p1.website_url and p2.website_url and p1.website_url == p2.website_url:
                reasons.append("same website")
            if p1.location_city and p2.location_city and p1.location_city == p2.location_city:
                reasons.append(f"same city ({p1.location_city})")
            if not reasons:
                reasons.append("name similarity")

            # Lower id is target (kept), higher id is source (merged/archived).
            target = p1 if p1.id < p2.id else p2
            source = p2 if p1.id < p2.id else p1

            pairs.append(
                {
                    "source": source,
                    "target": target,
                    "score": round(score, 3),
                    "reason": "; ".join(reasons),
                }
            )

            if len(pairs) >= limit:
                return pairs

    return pairs


def _fuzzy_score(a: str, b: str) -> float:
    """Simple Jaro-style overlap score (0..1) for name matching."""
    if not a or not b:
        return 0.0
    shorter, longer = sorted((a, b), key=len)
    if not shorter:
        return 0.0
    trigrams_short = {shorter[i : i + 3] for i in range(len(shorter) - 2)}
    if not trigrams_short:
        return 1.0 if shorter == longer else 0.0
    matches = sum(1 for i in range(len(longer) - 2) if longer[i : i + 3] in trigrams_short)
    trigram_score = matches / len(trigrams_short)
    length_ratio = len(shorter) / len(longer)
    return round((trigram_score * 0.7) + (length_ratio * 0.3), 3)


def merge_prospects(
    db: Session,
    *,
    source_id: int,
    target_id: int,
    actor: str = "user",
) -> tuple[Prospect | None, str | None]:
    """Merge ``source_id`` prospect into ``target_id`` prospect.

    The source prospect is archived (soft delete). All child rows are
    re-linked to the target. A merge activity is logged on both prospects.

    Returns (target_prospect, error_message).
    """
    source = db.get(Prospect, source_id)
    target = db.get(Prospect, target_id)

    if source is None or target is None:
        return None, "Prospect not found"
    if source_id == target_id:
        return None, "Cannot merge a prospect into itself"
    if source.archived_at is not None:
        return None, "Source prospect is already archived"
    if target.archived_at is not None:
        return None, "Target prospect is archived"

    # --- Re-link notes ---
    for note in source.notes:
        note.prospect_id = target_id

    # --- Re-link activities (append-only, keep history) ---
    for activity in source.activities:
        activity.prospect_id = target_id

    # --- Re-link findings ---
    for finding in source.findings:
        finding.prospect_id = target_id

    # --- Re-link website checks ---
    for check in source.website_checks:
        check.prospect_id = target_id

    # --- Re-link opportunities (and their opportunity_findings) ---
    for opp in source.opportunities:
        opp.prospect_id = target_id

    # --- Re-link outreach messages ---
    for msg in source.outreach_messages:
        msg.prospect_id = target_id

    # --- Re-link follow-ups ---
    for fu in source.follow_ups:
        fu.prospect_id = target_id

    # --- Re-link tags ---
    for tag in source.tags:
        if tag not in target.tags:
            target.tags.append(tag)

    # --- Re-link research runs ---
    for run in source.research_runs:
        run.prospect_id = target_id

    # --- Archive the source prospect ---
    source.archived_at = _now()

    # Log merge activity on both prospects.
    log_activity(
        db,
        prospect_id=target_id,
        type="merged",
        actor=actor,
        payload={
            "merged_into": target_id,
            "merged_from": source_id,
            "merged_from_name": source.business_name,
        },
    )
    log_activity(
        db,
        prospect_id=source_id,
        type="merged_into",
        actor=actor,
        payload={
            "merged_into": target_id,
            "merged_into_name": target.business_name,
        },
    )

    db.flush()
    return target, None
