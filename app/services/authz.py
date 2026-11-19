"""Role-based authorization policy (owner decision 2026-11-18).

Three roles, fail-closed for anything not listed:

* ``owner``  — everything, including user management.
* ``admin``  — operational surfaces incl. settings and the danger zone;
               cannot manage users.
* ``member`` — the operational canvas only (board, detail, outreach,
               research, notes): no settings, no danger zone, no users.

When the gate is off (loopback MVP, ``APP_ACCESS_TOKEN`` unset) the caller
bypasses these checks entirely — documented in SECURITY_AND_PRIVACY §3.

Prospect visibility (``owner_id`` scoping): rows owned by nobody (legacy /
worker-created) are shared; otherwise admins and owners see everything and
members see their own rows.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from app.models import Prospect


class Identity(Protocol):
    """Minimal identity shape for authorization decisions.

    Both ORM ``User`` rows and the lightweight view built by
    ``auth.current_user()`` satisfy it structurally — routes pass whichever
    they have without authz caring how the identity was loaded.
    """

    id: int
    role: str


#: Restricted surfaces -> roles allowed. Anything not listed here is
#: authenticated-only (no extra role required).
PERMISSIONS: dict[str, frozenset[str]] = {
    "settings": frozenset({"owner", "admin"}),
    "danger": frozenset({"owner", "admin"}),
    "users": frozenset({"owner"}),
}


#: User-facing 403 copy, keyed by permission.
PERMISSION_MESSAGES: dict[str, str] = {
    "settings": "Settings changes require an admin or owner account.",
    "danger": "This action requires an admin or owner account.",
    "users": "Only the owner can manage users.",
}


def has_permission(role: str | None, permission: str) -> bool:
    """Fail-closed role check (unknown role or permission -> False)."""
    if not role:
        return False
    return role in PERMISSIONS.get(permission, frozenset())


def can_see_prospect(user: Identity | None, prospect_owner_id: int | None) -> bool:
    """Ownership visibility: shared rows for everyone, owned rows for
    owner/admin or the owner themselves."""
    if user is None:
        return True  # gate off: loopback MVP has no identity to scope by
    if user.role in ("owner", "admin"):
        return True
    if prospect_owner_id is None:
        return True
    return prospect_owner_id == user.id


def scoped_prospect_ids(db: Session, user: Identity | None) -> list[int] | None:
    """IDs a member may see; ``None`` = no restriction (admin/owner/gate off)."""
    if user is None or user.role in ("owner", "admin"):
        return None
    rows = db.execute(
        select(Prospect.id).where(or_(Prospect.owner_id.is_(None), Prospect.owner_id == user.id))
    ).all()
    return [row[0] for row in rows]


def stamp_owner(user: Identity | None) -> int | None:
    """``owner_id`` to stamp on a row the given user creates.

    Members get their own id (private rows, visible to them + admins/owners);
    owner/admin/anonymous creates stay ``NULL`` = shared — the owner's pipeline
    is the team's pipeline, only a member's work is theirs alone.
    """
    if user is None or user.role != "member":
        return None
    return user.id


def scoped_prospect_subq(user: Identity | None) -> Select[int] | None:
    """``SELECT id`` limited to what ``user`` may see; ``None`` = unrestricted.

    For child-table queries (messages, findings, follow-ups, …) that must
    respect the same visibility as the prospect list.
    """
    crit = scope_criterion(user)
    if crit is None:
        return None
    return select(Prospect.id).where(crit)


def scope_criterion(user: Identity | None) -> ColumnElement[bool] | None:
    """SQL filter limiting prospects to those ``user`` may see.

    ``None`` = no restriction (gate off, admin, owner). Members get
    ``owner_id IS NULL OR owner_id = me`` — the SQL form of
    :func:`can_see_prospect`, pushed into queries so lists/facets/dupe
    checks never surface another member's rows.
    """
    if user is None or user.role in ("owner", "admin"):
        return None
    return or_(Prospect.owner_id.is_(None), Prospect.owner_id == user.id)


def visible_prospect(db: Session, user: Identity | None, prospect_id: int) -> Prospect | None:
    """Fetch a prospect the user may see (None = not found or not theirs)."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None
    if not can_see_prospect(user, prospect.owner_id):
        return None
    return prospect
