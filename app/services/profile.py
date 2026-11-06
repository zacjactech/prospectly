"""Profile service (single owner profile, DATABASE_DESIGN §2.15)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Profile


def get_or_create_profile(db: Session) -> Profile:
    """Return the single profile row, creating defaults if absent."""
    profile = db.query(Profile).first()
    if profile is None:
        profile = Profile(
            offer=None,
            target_industries=None,
            target_locations=None,
            ideal_signals=None,
        )
        db.add(profile)
        db.flush()
    return profile


def update_profile(
    db: Session,
    *,
    offer: str | None = None,
    target_industries: list[str] | None = None,
    target_locations: list[str] | None = None,
    ideal_signals: list[str] | None = None,
) -> Profile:
    """Upsert the profile fields that are provided; leave others untouched."""
    profile = get_or_create_profile(db)
    if offer is not None:
        profile.offer = offer
    if target_industries is not None:
        profile.target_industries = target_industries
    if target_locations is not None:
        profile.target_locations = target_locations
    if ideal_signals is not None:
        profile.ideal_signals = ideal_signals
    db.flush()
    return profile
