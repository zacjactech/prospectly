"""Saved views service (Phase 6+ free feature).

CRUD for named prospect list filter views. Stores filter criteria as JSON
so operators can quickly reload a filtered view of the prospects list.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import SavedView


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
    """Create a new saved view."""
    view = SavedView(name=name, filters=filters)
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
        view.filters = filters
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
