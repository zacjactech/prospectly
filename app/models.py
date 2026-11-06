"""SQLAlchemy ORM models for Prospectly (DATABASE_DESIGN §2).

All tables live in one SQLite file (WAL). Phase 1 creates:
profiles, sources, prospects, tags, prospect_tags, notes, activities,
discovery_jobs, jobs.

Relationships follow DATABASE_DESIGN §3 (PROTECT sources, CASCADE with
prospects, append-only activities).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Source(Base):
    """Where a prospect came from (§2.1)."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    prospects: Mapped[list[Prospect]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Tag(Base):
    """User-defined tags (§2.3)."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)

    prospects: Mapped[list[Prospect]] = relationship(
        secondary="prospect_tags", back_populates="tags"
    )


class ProspectTag(Base):
    """Many-to-many join between prospects and tags (§2.3)."""

    __tablename__ = "prospect_tags"

    prospect_id: Mapped[int] = mapped_column(
        ForeignKey("prospects.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class Prospect(Base):
    """A candidate client (§2.2)."""

    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location_area: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location_state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location_country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(String(30), nullable=True)
    phone_raw: Mapped[str | None] = mapped_column(String(80), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    social_links: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="new", index=True)
    website_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown", index=True
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="NO ACTION"), nullable=True, index=True
    )
    owner_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # Phase 6+
    research_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_contacted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    archived_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    do_not_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    do_not_contact_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    do_not_contact_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    source: Mapped[Source | None] = relationship(back_populates="prospects")
    tags: Mapped[list[Tag]] = relationship(secondary="prospect_tags", back_populates="prospects")
    notes = relationship("Note", back_populates="prospect")
    activities = relationship("Activity", back_populates="prospect", cascade="all, delete-orphan")

    __table_args__ = (
        # Dedupe partial unique indexes (§2.2, P9) — only among non-archived rows.
        Index(
            "uq_prospects_norm_name",
            "normalized_name",
            unique=True,
            postgresql_where=None,  # placeholder; SQLite uses sqlite_where below
            sqlite_where=(archived_at.is_(None)),
        ),
        Index(
            "uq_prospects_phone",
            "phone_e164",
            unique=True,
            sqlite_where=(archived_at.is_(None) & (phone_e164.is_not(None))),
        ),
        Index(
            "uq_prospects_email",
            "email",
            unique=True,
            sqlite_where=(archived_at.is_(None) & (email.is_not(None))),
        ),
        Index(
            "uq_prospects_domain",
            "website_url",
            unique=True,
            sqlite_where=(archived_at.is_(None) & (website_url.is_not(None))),
        ),
        Index("ix_prospects_archived_stage_updated", "archived_at", "stage", "updated_at"),
    )


class Note(Base):
    """Editable notes on a prospect (§2.4)."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prospect_id: Mapped[int] = mapped_column(
        ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    edited_from: Mapped[str | None] = mapped_column(String(40), nullable=True)

    prospect: Mapped[Prospect] = relationship(back_populates="notes")


class Profile(Base):
    """Single owner profile (§2.15) — drives discovery + draft personalization."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    offer: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_industries: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    target_locations: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    ideal_signals: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DiscoveryJob(Base):
    """Automated discovery run keyed to a profile (§2.16)."""

    __tablename__ = "discovery_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prospects_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Job(Base):
    """Generic DB-backed job queue (§2.12, D5)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    run_after: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)


class Activity(Base):
    """Append-only activity history (§2.11)."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prospect_id: Mapped[int] = mapped_column(
        ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user"
    )  # 'user' | 'system' | 'ai'
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    prospect: Mapped[Prospect] = relationship(back_populates="activities")

    __table_args__ = (Index("ix_activities_prospect_created", "prospect_id", "created_at"),)
