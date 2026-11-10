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
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import (
    Prospect,
)
from app.services.activity import log_activity
from app.services.prospect import _normalize_name, set_do_not_contact

# --- Fuzzy-duplicate candidate prefilter bounds (M1, review finding) ---
# Scoring every live pair is O(n^2): 5,000 prospects meant ~12.5M name scores
# in a single request. Instead we bucket prospects by cheap match keys
# (normalized name, name tokens, phone, email, website domain) and only score
# pairs that share at least one bucket. The bounds below keep the worst case
# (pathological names sharing very common tokens) linear in practice.
MAX_CANDIDATE_PAIRS = 20_000
"""Hard cap on pairs scored per call. Ordering is deterministic (bucket key,
then prospect id), so results are stable across runs."""

MAX_BUCKET_SIZE = 200
"""A bucket larger than this is too generic to imply a plausible duplicate
(e.g. 5,000 prospects all containing the token "shop"); it is skipped."""

_NAME_TOKEN_MIN_LEN = 3
"""Name tokens shorter than this are ignored as bucket keys."""


def _now() -> datetime:
    return datetime.now(UTC)


def _website_domain(url: str | None) -> str | None:
    """Return the bare lowercase host for a website URL (``www.`` stripped)."""
    if not url:
        return None
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:  # pragma: no cover - urlparse rarely raises
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _name_tokens(normalized_name: str) -> set[str]:
    return {token for token in normalized_name.split() if len(token) >= _NAME_TOKEN_MIN_LEN}


def _candidate_keys(prospect: Prospect) -> list[tuple[str, str]]:
    """Cheap, normalized keys used to bucket a prospect for pair prefiltering.

    Two prospects that share a key *may* be duplicates; the fuzzy name score
    still decides. Keys are computed from the same normalized values the dedupe
    path stores (see ``prospect._normalize_name``), so the buckets cannot drift
    from the persisted dedupe keys.
    """
    keys: list[tuple[str, str]] = []

    name = prospect.normalized_name or _normalize_name(prospect.business_name)
    if name:
        keys.append(("name", name))
        keys.extend(("token", token) for token in _name_tokens(name))

    if prospect.phone_e164:
        keys.append(("phone", prospect.phone_e164))
    if prospect.email:
        keys.append(("email", prospect.email.lower()))

    domain = _website_domain(prospect.website_url)
    if domain:
        keys.append(("domain", domain))

    return keys


def _candidate_pairs(
    prospects: list[Prospect],
    *,
    max_pairs: int = MAX_CANDIDATE_PAIRS,
) -> list[tuple[int, int]]:
    """Return id pairs sharing at least one bucket, excluding no-op buckets.

    ``prospects`` must be ordered by id. The result is sorted by (low id, high
    id) — the same order the previous all-pairs scan produced — and capped at
    ``max_pairs`` after de-duplication.
    """
    buckets: dict[tuple[str, str], list[int]] = {}
    for prospect in prospects:
        for key in _candidate_keys(prospect):
            buckets.setdefault(key, []).append(prospect.id)

    seen: set[tuple[int, int]] = set()
    pairs: list[tuple[int, int]] = []

    # Sorted keys make the cap deterministic when it is reached.
    for key in sorted(buckets):
        ids = buckets[key]
        if len(ids) < 2 or len(ids) > MAX_BUCKET_SIZE:
            continue
        for i, first in enumerate(ids):
            for second in ids[i + 1 :]:
                pair = (first, second) if first < second else (second, first)
                if pair in seen:
                    continue
                seen.add(pair)
                pairs.append(pair)
                if len(pairs) >= max_pairs:
                    return sorted(pairs)

    return sorted(pairs)


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

    Candidate pairs are prefiltered by shared normalized keys (name, name
    tokens, phone, email, website domain) so only plausible pairs are scored;
    see ``MAX_CANDIDATE_PAIRS`` / ``MAX_BUCKET_SIZE`` for the hard bounds.
    """
    prospects = (
        db.query(Prospect).filter(Prospect.archived_at.is_(None)).order_by(Prospect.id.asc()).all()
    )
    by_id = {prospect.id: prospect for prospect in prospects}

    pairs: list[dict[str, Any]] = []

    for low_id, high_id in _candidate_pairs(prospects):
        p1 = by_id[low_id]
        p2 = by_id[high_id]

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
    # findings carry a unique (prospect_id, finding_key). When the target
    # already holds the identical idempotent finding — guaranteed for any
    # two researched no-URL prospects, which both record the constant
    # "No website URL recorded" observation — re-linking would violate the
    # index and 500 the merge (reviewer N1). Keep the target's copy; the
    # source's copy stays on the archived source row (no data is destroyed,
    # the archived prospect keeps its audit trail).
    target_keys = {f.finding_key for f in target.findings}
    for finding in list(source.findings):
        if finding.finding_key in target_keys:
            continue
        finding.prospect_id = target_id
        target_keys.add(finding.finding_key)

    # --- Re-link website checks ---
    for check in source.website_checks:
        check.prospect_id = target_id

    # --- Re-link opportunities (and their opportunity_findings) ---
    for opp in source.opportunities:
        opp.prospect_id = target_id

    # --- Re-link outreach messages ---
    # §2.9: only one ACTIVE message per prospect (partial unique index) —
    # if both sides have an active message, the target's wins.
    for msg in source.outreach_messages:
        if msg.active and any(m.active for m in target.outreach_messages):
            msg.active = False
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

    # --- Propagate suppression (Flow 8 / P10) ---
    # Merging a suppressed duplicate into a live one must not silently
    # restore contact permission — the opt-out travels with the identity.
    if source.do_not_contact and not target.do_not_contact:
        set_do_not_contact(
            db,
            target_id,
            reason=source.do_not_contact_reason or "merged duplicate was suppressed",
            actor=actor,
        )

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
