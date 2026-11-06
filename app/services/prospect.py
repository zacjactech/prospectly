"""Prospect service: CRUD, dedupe, stage transitions, activity logging.

Public functions return `(prospect, error)` tuples; callers translate to
HTTP responses. Stage transitions are validated against the state machine
(SYSTEM_ARCHITECTURE §3.2); illegal transitions raise ValueError so routes
can return 409.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models import Prospect, Source, Tag
from app.services.activity import log_activity

# Allowed stage transitions (SYSTEM_ARCHITECTURE §3.2).
_STAGE_TRANSITIONS: dict[str, set[str]] = {
    "new": {"researched", "archived"},
    "researched": {"opportunity_identified", "archived"},
    "opportunity_identified": {"outreach_ready", "archived"},
    "outreach_ready": {"contacted", "archived"},
    "contacted": {"replied", "follow_up_due", "archived"},
    "replied": {"interested", "follow_up_due", "archived"},
    "interested": {"converted", "follow_up_due", "archived"},
    "follow_up_due": {"replied", "archived"},
    "converted": {"archived"},
    "not_interested": {"archived"},
    "do_not_contact": {"archived"},  # terminal; only archive out
    "archived": set(),  # terminal
}


class DuplicateError(ValueError):
    """Raised when a prospect fails dedupe (exact match blocked)."""


class StageTransitionError(ValueError):
    """Raised when a stage transition is not allowed."""


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for dedupe."""
    import re

    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    return " ".join(cleaned.split())


def _fuzzy_score(a: str, b: str) -> float:
    """Simple Jaro-style overlap score (0..1) for same-city name matching."""
    if not a or not b:
        return 0.0
    shorter, longer = sorted((a, b), key=len)
    if not shorter:
        return 0.0
    # Trigram overlap + length ratio.
    trigrams_short = {shorter[i : i + 3] for i in range(len(shorter) - 2)}
    if not trigrams_short:
        return 1.0 if shorter == longer else 0.0
    matches = sum(1 for i in range(len(longer) - 2) if longer[i : i + 3] in trigrams_short)
    trigram_score = matches / len(trigrams_short)
    length_ratio = len(shorter) / len(longer)
    return round((trigram_score * 0.7) + (length_ratio * 0.3), 3)


def check_duplicates(
    db: Session,
    *,
    business_name: str,
    phone_e164: str | None = None,
    email: str | None = None,
    website_url: str | None = None,
    location_city: str | None = None,
    exclude_id: int | None = None,
) -> dict[str, Any]:
    """Return dedupe assessment: exact match (block) or fuzzy flags (warn).

    Never silently merges. Caller decides whether to block or proceed.
    """
    normalized_name = _normalize_name(business_name)
    exact: Prospect | None = None
    fuzzy: list[Prospect] = []

    q = db.query(Prospect).filter(Prospect.archived_at.is_(None))
    if exclude_id is not None:
        q = q.filter(Prospect.id != exclude_id)

    # Exact key matches.
    if phone_e164:
        exact = q.filter(Prospect.phone_e164 == phone_e164).first()
    if exact is None and email:
        exact = q.filter(Prospect.email == email.lower()).first()
    if exact is None and website_url:
        exact = q.filter(Prospect.website_url == website_url).first()
    if exact is None and normalized_name:
        exact = q.filter(Prospect.normalized_name == normalized_name).first()

    # Fuzzy: same city + high name overlap.
    if location_city and normalized_name:
        same_city = q.filter(Prospect.location_city == location_city).all()
        for p in same_city:
            if p.normalized_name and _fuzzy_score(normalized_name, p.normalized_name) >= 0.85:
                fuzzy.append(p)

    return {
        "exact": exact,
        "fuzzy": fuzzy,
        "normalized_name": normalized_name,
    }


def create_prospect(
    db: Session,
    *,
    business_name: str,
    source: Source | None = None,
    category: str | None = None,
    location_area: str | None = None,
    location_city: str | None = None,
    location_state: str | None = None,
    location_country: str | None = None,
    address: str | None = None,
    phone_e164: str | None = None,
    phone_raw: str | None = None,
    email: str | None = None,
    website_url: str | None = None,
    social_links: dict | None = None,
    actor: str = "user",
    skip_dupe_check: bool = False,
) -> tuple[Prospect | None, str | None]:
    """Create a prospect. Blocks on exact dupe match; flags fuzzy via returned error message."""
    dupe = check_duplicates(
        db,
        business_name=business_name,
        phone_e164=phone_e164,
        email=email,
        website_url=website_url,
        location_city=location_city,
    )
    if dupe["exact"] is not None and not skip_dupe_check:
        return None, (
            f"Duplicate prospect already exists: {dupe['exact'].business_name} "
            f"(id={dupe['exact'].id})"
        )

    prospect = Prospect(
        business_name=business_name,
        normalized_name=dupe["normalized_name"],
        category=category,
        location_area=location_area,
        location_city=location_city,
        location_state=location_state,
        location_country=location_country,
        address=address,
        phone_e164=phone_e164,
        phone_raw=phone_raw,
        email=email.lower() if email else None,
        website_url=website_url,
        social_links=social_links,
        stage="new",
        website_status="unknown",
        source=source,
        do_not_contact=False,
    )
    db.add(prospect)
    db.flush()

    log_activity(
        db,
        prospect_id=prospect.id,
        type="created",
        actor=actor,
        payload={
            "category": category,
            "location_city": location_city,
            "source_type": source.type if source else None,
            "fuzzy_flags": [p.id for p in dupe["fuzzy"]],
        },
    )
    return prospect, None


def update_prospect(
    db: Session,
    prospect_id: int,
    *,
    actor: str = "user",
    **fields: Any,
) -> tuple[Prospect | None, str | None]:
    """Update scalar fields on a prospect. Re-checks dedupe on key fields."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"

    changed: dict[str, Any] = {}
    for name, value in fields.items():
        if not hasattr(prospect, name):
            return None, f"Unknown field: {name}"
        if getattr(prospect, name) != value:
            changed[name] = value

    # Re-run dedupe if identity keys changed.
    if any(
        k in changed
        for k in ("business_name", "phone_e164", "email", "website_url", "location_city")
    ):
        dupe = check_duplicates(
            db,
            business_name=changed.get("business_name", prospect.business_name),
            phone_e164=changed.get("phone_e164", prospect.phone_e164),
            email=changed.get("email", prospect.email),
            website_url=changed.get("website_url", prospect.website_url),
            location_city=changed.get("location_city", prospect.location_city),
            exclude_id=prospect.id,
        )
        if dupe["exact"] is not None:
            return None, (
                f"Would duplicate existing prospect: {dupe['exact'].business_name} "
                f"(id={dupe['exact'].id})"
            )
        if dupe["fuzzy"]:
            changed["_fuzzy_flags"] = [p.id for p in dupe["fuzzy"]]

    for name, value in changed.items():
        setattr(prospect, name, value)

    db.flush()
    log_activity(
        db,
        prospect_id=prospect.id,
        type="edited",
        actor=actor,
        payload={k: v for k, v in changed.items() if not k.startswith("_")},
    )
    return prospect, None


def archive_prospect(db: Session, prospect_id: int, *, actor: str = "user") -> bool:
    """Soft-archive a prospect (sets archived_at). Releases dedupe keys."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return False
    prospect.archived_at = _now()
    db.flush()
    log_activity(
        db,
        prospect_id=prospect.id,
        type="archived",
        actor=actor,
    )
    return True


def unarchive_prospect(db: Session, prospect_id: int, *, actor: str = "user") -> bool:
    """Restore an archived prospect (re-checks dedupe against live rows)."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return False
    # Re-check dedupe before releasing the archived key.
    dupe = check_duplicates(
        db,
        business_name=prospect.business_name,
        phone_e164=prospect.phone_e164,
        email=prospect.email,
        website_url=prospect.website_url,
        location_city=prospect.location_city,
        exclude_id=prospect.id,
    )
    if dupe["exact"] is not None:
        return False  # caller translates to 409
    prospect.archived_at = None
    db.flush()
    log_activity(
        db,
        prospect_id=prospect.id,
        type="unarchived",
        actor=actor,
    )
    return True


def transition_stage(
    db: Session,
    prospect_id: int,
    new_stage: str,
    *,
    actor: str = "user",
) -> tuple[Prospect | None, str | None]:
    """Validate and apply a stage transition per the state machine."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"
    if prospect.do_not_contact and new_stage not in {"archived"}:
        return None, "Cannot change stage on a do_not_contact prospect"

    allowed = _STAGE_TRANSITIONS.get(prospect.stage, set())
    if new_stage not in allowed:
        return None, (f"Illegal stage transition {prospect.stage} → {new_stage}")

    old_stage = prospect.stage
    prospect.stage = new_stage
    if new_stage == "contacted":
        prospect.last_contacted_at = _now()
    db.flush()
    log_activity(
        db,
        prospect_id=prospect.id,
        type="stage_changed",
        actor=actor,
        payload={"from": old_stage, "to": new_stage},
    )
    return prospect, None


def set_do_not_contact(
    db: Session,
    prospect_id: int,
    *,
    reason: str,
    actor: str = "user",
) -> tuple[Prospect | None, str | None]:
    """Terminal suppression flag; blocks all outbound actions."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"
    prospect.do_not_contact = True
    prospect.do_not_contact_reason = reason
    prospect.do_not_contact_at = _now()
    db.flush()
    log_activity(
        db,
        prospect_id=prospect.id,
        type="do_not_contact_set",
        actor=actor,
        payload={"reason": reason},
    )
    return prospect, None


def clear_do_not_contact(
    db: Session,
    prospect_id: int,
    *,
    actor: str = "user",
) -> tuple[Prospect | None, str | None]:
    """Clear suppression (explicit action + reason, audited)."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"
    prospect.do_not_contact = False
    prospect.do_not_contact_reason = None
    prospect.do_not_contact_at = None
    db.flush()
    log_activity(
        db,
        prospect_id=prospect.id,
        type="do_not_contact_cleared",
        actor=actor,
    )
    return prospect, None


def add_tag(db: Session, prospect_id: int, tag_name: str, *, actor: str = "user") -> bool:
    """Attach a tag to a prospect, creating the tag if needed."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return False
    tag = db.query(Tag).filter(Tag.name == tag_name).first()
    if tag is None:
        tag = Tag(name=tag_name)
        db.add(tag)
        db.flush()
    if tag not in prospect.tags:
        prospect.tags.append(tag)
        db.flush()
        log_activity(
            db,
            prospect_id=prospect.id,
            type="tag_added",
            actor=actor,
            payload={"tag": tag_name},
        )
    return True


def list_prospects(
    db: Session,
    *,
    stage: str | None = None,
    tag: str | None = None,
    category: str | None = None,
    location: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
    do_not_contact_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Prospect]:
    """Filtered prospect list (used by both pages and API)."""
    query = db.query(Prospect).options(selectinload(Prospect.tags))
    if not include_archived:
        query = query.filter(Prospect.archived_at.is_(None))
    if stage:
        query = query.filter(Prospect.stage == stage)
    if do_not_contact_only:
        query = query.filter(Prospect.do_not_contact.is_(True))
    if tag:
        query = query.join(Prospect.tags).filter(Tag.name == tag)
    if category:
        query = query.filter(Prospect.category == category)
    if location:
        query = query.filter(
            (Prospect.location_city == location)
            | (Prospect.location_area == location)
            | (Prospect.location_state == location)
        )
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Prospect.business_name.ilike(like))
            | (Prospect.category.ilike(like))
            | (Prospect.location_city.ilike(like))
        )
    query = query.order_by(Prospect.updated_at.desc())
    return query.offset(offset).limit(limit).all()
