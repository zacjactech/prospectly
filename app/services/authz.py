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

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Prospect, User

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


def can_see_prospect(user: User | None, prospect_owner_id: int | None) -> bool:
    """Ownership visibility: shared rows for everyone, owned rows for
    owner/admin or the owner themselves."""
    if user is None:
        return True  # gate off: loopback MVP has no identity to scope by
    if user.role in ("owner", "admin"):
        return True
    if prospect_owner_id is None:
        return True
    return prospect_owner_id == user.id


def scoped_prospect_ids(db: Session, user: User | None) -> list[int] | None:
    """IDs a member may see; ``None`` = no restriction (admin/owner/gate off)."""
    if user is None or user.role in ("owner", "admin"):
        return None
    rows = db.execute(
        select(Prospect.id).where(or_(Prospect.owner_id.is_(None), Prospect.owner_id == user.id))
    ).all()
    return [row[0] for row in rows]


def visible_prospect(db: Session, user: User | None, prospect_id: int) -> Prospect | None:
    """Fetch a prospect the user may see (None = not found or not theirs)."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return None
    if not can_see_prospect(user, prospect.owner_id):
        return None
    return prospect
