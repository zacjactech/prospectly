"""Prospect service: CRUD, dedupe, stage transitions, activity logging.

Public functions return `(prospect, error)` tuples; callers translate to
HTTP responses. Stage transitions are validated against the state machine
(SYSTEM_ARCHITECTURE §3.2); illegal transitions raise ValueError so routes
can return 409.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Prospect, Source, Tag
from app.services import authz
from app.services.activity import log_activity

# Allowed stage transitions (SYSTEM_ARCHITECTURE §3.2).
#
# Two classes of inbound edges beyond the linear pipeline:
# - Event edges: confirming a send (→ contacted) and recording a reply
#   (→ replied) are operator-recorded facts (USER_FLOWS Flow 5.8 / 6.2) and
#   may land from any active pre-contact stage — the pipeline position may
#   lag reality because stages are otherwise advanced manually.
# - Dispositions: not_interested and do_not_contact are available at any
#   point from an active stage (Flow 6.3 "terminal for outreach", Flow 8
#   "Any time"); do_not_contact also sets the suppression flag in
#   transition_stage (guards read the flag — P10).
_STAGE_TRANSITIONS: dict[str, set[str]] = {
    "new": {"researched", "contacted", "replied", "not_interested", "do_not_contact", "archived"},
    "researched": {
        "opportunity_identified",
        "contacted",
        "replied",
        "not_interested",
        "do_not_contact",
        "archived",
    },
    "opportunity_identified": {
        "outreach_ready",
        "contacted",
        "replied",
        "not_interested",
        "do_not_contact",
        "archived",
    },
    "outreach_ready": {"contacted", "replied", "not_interested", "do_not_contact", "archived"},
    "contacted": {"replied", "follow_up_due", "not_interested", "do_not_contact", "archived"},
    "replied": {"interested", "follow_up_due", "not_interested", "do_not_contact", "archived"},
    "interested": {"converted", "follow_up_due", "not_interested", "do_not_contact", "archived"},
    "follow_up_due": {"replied", "not_interested", "do_not_contact", "archived"},
    "converted": {"do_not_contact", "archived"},
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
    visible_to: authz.Identity | None = None,
) -> dict[str, Any]:
    """Return dedupe assessment: exact match (block) or fuzzy flags (warn).

    Never silently merges. Caller decides whether to block or proceed.

    ``visible_to`` scopes the candidate pool to what that user may see
    (authz.scope_criterion) — a member's dupe check must not reveal, or be
    blocked by, another member's private row.
    """
    normalized_name = _normalize_name(business_name)
    exact: Prospect | None = None
    exact_field: str | None = None
    fuzzy: list[Prospect] = []

    q = db.query(Prospect).filter(Prospect.archived_at.is_(None))
    if exclude_id is not None:
        q = q.filter(Prospect.id != exclude_id)
    scope = authz.scope_criterion(visible_to)
    if scope is not None:
        q = q.filter(scope)

    # Exact key matches (order matters: it mirrors which partial unique index
    # would reject the write, so `exact_field` names the same key the
    # IntegrityError backstop reports).
    if phone_e164:
        exact = q.filter(Prospect.phone_e164 == phone_e164).first()
        if exact is not None:
            exact_field = "phone_e164"
    if exact is None and email:
        exact = q.filter(Prospect.email == email.lower()).first()
        if exact is not None:
            exact_field = "email"
    if exact is None and website_url:
        exact = q.filter(Prospect.website_url == website_url).first()
        if exact is not None:
            exact_field = "website_url"
    if exact is None and normalized_name:
        exact = q.filter(Prospect.normalized_name == normalized_name).first()
        if exact is not None:
            exact_field = "normalized_name"

    # Fuzzy: same city + high name overlap.
    if location_city and normalized_name:
        same_city = q.filter(Prospect.location_city == location_city).all()
        for p in same_city:
            if p.normalized_name and _fuzzy_score(normalized_name, p.normalized_name) >= 0.85:
                fuzzy.append(p)

    return {
        "exact": exact,
        "exact_field": exact_field,
        "fuzzy": fuzzy,
        "normalized_name": normalized_name,
    }


#: Unique-index columns on `prospects` (§2.2) -> human label for the
#: conflict message. The scoped dupe check cannot see other members' private
#: rows, but the *global* partial unique indexes still reject the write; the
#: backstop turns that rejection into a leak-free message (never names the
#: invisible row).
_IDENTITY_COLUMNS: dict[str, str] = {
    "phone_e164": "phone number",
    "email": "email address",
    "website_url": "website",
    "normalized_name": "business name",
}


def _identity_conflict_message(exc: IntegrityError) -> str | None:
    """Map a unique-index violation to a leak-free duplicate message.

    Returns ``None`` for anything that is NOT an identity-key collision — an
    unexpected constraint failure must surface as a 500, not masquerade as a
    duplicate (fail closed).
    """
    text = str(getattr(exc, "orig", exc))
    for column, label in _IDENTITY_COLUMNS.items():
        if f"prospects.{column}" in text:
            return f"A prospect with this {label} already exists."
    return None


def _conflict_message(
    dupe: dict[str, Any], *, visible_to: authz.Identity | None, detail: str
) -> str:
    """Duplicate message for a pre-check hit, safe for a scoped caller.

    An unscoped caller (owner/admin, gate off) sees every row, so naming the
    conflicting row is safe and useful. A scoped caller (member) sees only
    *some* rows: if this path named the row while the unique-index backstop
    cannot, the difference would tell a member "this conflict exists but is
    hidden from you" — an existence oracle. Scoped callers therefore always
    get the backstop's leak-free copy: same field label, never the row.
    """
    if authz.scope_criterion(visible_to) is None:
        return detail
    label = _IDENTITY_COLUMNS.get(dupe.get("exact_field") or "", "business name")
    return f"A prospect with this {label} already exists."


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
    owner_id: int | None = None,
    visible_to: authz.Identity | None = None,
) -> tuple[Prospect | None, str | None]:
    """Create a prospect. Blocks on exact dupe match; flags fuzzy via returned error message.

    ``owner_id`` stamps prospect ownership for member-created rows (authz
    scoping; ``None`` = shared). ``visible_to`` scopes the dupe check to the
    creator's visible set.
    """
    dupe = check_duplicates(
        db,
        business_name=business_name,
        phone_e164=phone_e164,
        email=email,
        website_url=website_url,
        location_city=location_city,
        visible_to=visible_to,
    )
    if dupe["exact"] is not None and not skip_dupe_check:
        return None, _conflict_message(
            dupe,
            visible_to=visible_to,
            detail=(
                f"Duplicate prospect already exists: {dupe['exact'].business_name} "
                f"(id={dupe['exact'].id})"
            ),
        )

    try:
        # Savepoint: the scoped dupe check above cannot see other members'
        # private rows, so an exact cross-member match reaches the global
        # unique index (§2.2). Roll back only this insert — earlier flushed
        # work in the caller's transaction (e.g. the import loop) survives.
        #
        # The row is also *constructed* inside the savepoint: building it
        # outside appends the transient prospect to the persistent
        # Source.prospects collection, which the savepoint snapshot does not
        # cover and SQLAlchemy warns about ("add operation ... will not
        # proceed") at flush time.
        with db.begin_nested():
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
                owner_id=owner_id,
            )
            db.add(prospect)
            db.flush()
    except IntegrityError as exc:
        message = _identity_conflict_message(exc)
        if message is None:
            raise
        return None, message

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
    visible_to: authz.Identity | None = None,
    **fields: Any,
) -> tuple[Prospect | None, str | None]:
    """Update scalar fields on a prospect. Re-checks dedupe on key fields.

    ``visible_to`` scopes the dedupe re-check so the error message can never
    name a row the caller may not see.
    """
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"

    changed: dict[str, Any] = {}
    for name, value in fields.items():
        if not hasattr(prospect, name):
            return None, f"Unknown field: {name}"
        if getattr(prospect, name) != value:
            changed[name] = value

    # Keep the dedupe key in step with the name. Without this the partial
    # unique index never fires on a rename: a scoped caller could rename into
    # another member's exact match (the scoped pre-check cannot see it and
    # `normalized_name` stayed stale), and the old key stayed behind forever
    # blocking a name nobody holds any more.
    if "business_name" in changed:
        changed["normalized_name"] = _normalize_name(changed["business_name"])

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
            visible_to=visible_to,
            exclude_id=prospect.id,
        )
        if dupe["exact"] is not None:
            return None, _conflict_message(
                dupe,
                visible_to=visible_to,
                detail=(
                    f"Would duplicate existing prospect: {dupe['exact'].business_name} "
                    f"(id={dupe['exact'].id})"
                ),
            )
        if dupe["fuzzy"]:
            changed["_fuzzy_flags"] = [p.id for p in dupe["fuzzy"]]

    try:
        # Savepoint (see create_prospect): a cross-member exact match the
        # scoped check could not see still hits the global unique index.
        # Rolling back restores the pre-edit attribute values.
        with db.begin_nested():
            for name, value in changed.items():
                setattr(prospect, name, value)
            db.flush()
    except IntegrityError as exc:
        message = _identity_conflict_message(exc)
        if message is None:
            raise
        return None, message

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


def unarchive_prospect(
    db: Session,
    prospect_id: int,
    *,
    actor: str = "user",
    visible_to: authz.Identity | None = None,
) -> bool:
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
        visible_to=visible_to,
        exclude_id=prospect.id,
    )
    if dupe["exact"] is not None:
        return False  # caller translates to 409
    try:
        # Savepoint (see create_prospect): re-adding the row to the partial
        # unique indexes can collide with a row the scoped check could not
        # see. Rollback restores archived_at; caller translates to 409.
        with db.begin_nested():
            prospect.archived_at = None
            db.flush()
    except IntegrityError as exc:
        if _identity_conflict_message(exc) is None:
            raise
        return False  # caller translates to 409
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
    # Suppression IS the flag (P10 — guards, badges and dashboard read it).
    # A stage-only move would display the prospect as suppressed while
    # outbound stays unblocked, so the flag must follow.
    if new_stage == "do_not_contact" and not prospect.do_not_contact:
        prospect.do_not_contact = True
        prospect.do_not_contact_at = _now()
        log_activity(
            db,
            prospect_id=prospect.id,
            type="do_not_contact_set",
            actor=actor,
            payload={"reason": prospect.do_not_contact_reason or "", "from_stage": old_stage},
        )
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
    """Terminal suppression flag; blocks all outbound actions (Flow 8).

    Also moves the stage to ``do_not_contact`` (unless archived) so §3.2,
    list filters and badges agree with the flag; the pre-suppression stage is
    recorded in the activity payload for a clean revert on clear.
    """
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"
    if prospect.do_not_contact:
        return None, "Prospect is already suppressed"
    old_stage = prospect.stage
    prospect.do_not_contact = True
    prospect.do_not_contact_reason = reason
    prospect.do_not_contact_at = _now()
    if old_stage not in ("do_not_contact", "archived"):
        prospect.stage = "do_not_contact"
    db.flush()
    log_activity(
        db,
        prospect_id=prospect.id,
        type="do_not_contact_set",
        actor=actor,
        payload={"reason": reason, "from_stage": old_stage},
    )
    if prospect.stage != old_stage:
        log_activity(
            db,
            prospect_id=prospect.id,
            type="stage_changed",
            actor=actor,
            payload={"from": old_stage, "to": prospect.stage},
        )
    return prospect, None


def _stage_before_suppression(db: Session, prospect: Prospect) -> str:
    """Best-effort recovery of the stage the prospect held when suppressed."""
    from app.models import Activity

    set_activity = (
        db.query(Activity)
        .filter_by(prospect_id=prospect.id, type="do_not_contact_set")
        .order_by(Activity.created_at.desc())
        .first()
    )
    if set_activity is not None:
        candidate = (set_activity.payload or {}).get("from_stage")
        if candidate and candidate in _STAGE_TRANSITIONS:
            return candidate
    last_stage = (
        db.query(Activity)
        .filter_by(prospect_id=prospect.id, type="stage_changed")
        .order_by(Activity.created_at.desc())
        .all()
    )
    for activity in last_stage:
        to_stage = (activity.payload or {}).get("to")
        if to_stage and to_stage != "do_not_contact" and to_stage in _STAGE_TRANSITIONS:
            return to_stage
    return "new"


def clear_do_not_contact(
    db: Session,
    prospect_id: int,
    *,
    reason: str = "",
    actor: str = "user",
) -> tuple[Prospect | None, str | None]:
    """Clear suppression (explicit action + reason, audited — Flow 8).

    Restores the pre-suppression stage so the prospect re-enters the
    pipeline where they left it.
    """
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None, "Prospect not found"
    if not prospect.do_not_contact:
        return None, "Prospect is not suppressed"
    old_stage = prospect.stage
    prospect.do_not_contact = False
    prospect.do_not_contact_reason = None
    prospect.do_not_contact_at = None
    if old_stage == "do_not_contact":
        prospect.stage = _stage_before_suppression(db, prospect)
    db.flush()
    log_activity(
        db,
        prospect_id=prospect.id,
        type="do_not_contact_cleared",
        actor=actor,
        payload={"reason": reason},
    )
    if prospect.stage != old_stage:
        log_activity(
            db,
            prospect_id=prospect.id,
            type="stage_changed",
            actor=actor,
            payload={"from": old_stage, "to": prospect.stage, "reason": "suppression_cleared"},
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
    visible_to: authz.Identity | None = None,
) -> list[Prospect]:
    """Filtered prospect list (used by both pages and API).

    ``visible_to`` applies the ownership scope (members see shared + own).
    """
    query = db.query(Prospect).options(selectinload(Prospect.tags))
    if not include_archived:
        query = query.filter(Prospect.archived_at.is_(None))
    scope = authz.scope_criterion(visible_to)
    if scope is not None:
        query = query.filter(scope)
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
