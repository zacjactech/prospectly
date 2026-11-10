"""Saved views service (Phase 6+ free feature).

CRUD for named prospect list filter views. Stores filter criteria as JSON
so operators can quickly reload a filtered view of the prospects list.

Filters are validated against the prospect list's actual query parameters:
unknown keys are dropped on save and on apply, so a crafted view can never
inject arbitrary query params into the list URL (review M3).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import SavedView

# The prospect list's real query parameters (routes/pages.py prospects_list).
ALLOWED_FILTER_KEYS = frozenset(
    {
        "stage",
        "tag",
        "category",
        "location",
        "q",
        "include_archived",
        "do_not_contact_only",
    }
)
_STRING_KEYS = frozenset({"stage", "tag", "category", "location", "q"})
_BOOL_KEYS = frozenset({"include_archived", "do_not_contact_only"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_MAX_FILTER_LEN = 200


def sanitize_filters(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only known list-filter keys, coerced to sane types.

    Returns None when nothing usable remains.
    """
    if not filters:
        return None
    clean: dict[str, Any] = {}
    for key, value in filters.items():
        if key not in ALLOWED_FILTER_KEYS:
            continue
        if key in _STRING_KEYS:
            if not isinstance(value, str):
                value = "" if value is None else str(value)
            value = value.strip()
            if not value:
                continue
            clean[key] = value[:_MAX_FILTER_LEN]
        else:  # bool keys
            if isinstance(value, bool):
                clean[key] = value
            elif isinstance(value, str):
                clean[key] = value.strip().lower() in _TRUTHY
            else:
                clean[key] = bool(value)
            if not clean[key]:
                clean.pop(key, None)  # defaults are False — don't store noise
    return clean or None


def unknown_filter_keys(filters: dict[str, Any] | None) -> list[str]:
    """Return sorted keys not understood by the prospect list (for 400s)."""
    if not filters:
        return []
    return sorted(k for k in filters if k not in ALLOWED_FILTER_KEYS)


def list_saved_views(db: Session) -> list[SavedView]:
    """Return all saved views, ordered by name."""
    return db.query(SavedView).order_by(SavedView.name.asc()).all()


def get_saved_view(db: Session, view_id: int) -> SavedView | None:
    """Return a saved view by ID, or None if not found."""
    return db.get(SavedView, view_id)


def create_saved_view(
    db: Session,
    *,
    name: str,
    filters: dict | None = None,
) -> SavedView:
    """Create a new saved view (filters sanitized to the list's keys)."""
    view = SavedView(name=name, filters=sanitize_filters(filters))
    db.add(view)
    db.flush()
    return view


def update_saved_view(
    db: Session,
    view_id: int,
    *,
    name: str | None = None,
    filters: dict | None = None,
) -> SavedView | None:
    """Update a saved view's name and/or filters. Returns None if not found."""
    view = db.get(SavedView, view_id)
    if view is None:
        return None
    if name is not None:
        view.name = name
    if filters is not None:
        view.filters = sanitize_filters(filters)
    db.flush()
    return view


def delete_saved_view(db: Session, view_id: int) -> bool:
    """Delete a saved view. Returns False if not found."""
    view = db.get(SavedView, view_id)
    if view is None:
        return False
    db.delete(view)
    db.flush()
    return True
