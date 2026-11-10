"""Pydantic DTOs for the JSON API (API_DESIGN §2, DATABASE_DESIGN §2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProspectBase(BaseModel):
    business_name: str = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    location_area: str | None = Field(default=None, max_length=100)
    location_city: str | None = Field(default=None, max_length=100)
    location_state: str | None = Field(default=None, max_length=100)
    location_country: str | None = Field(default=None, max_length=10)
    address: str | None = Field(default=None)
    phone_e164: str | None = Field(default=None, max_length=30)
    phone_raw: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=254)
    website_url: str | None = Field(default=None, max_length=2000)
    social_links: dict[str, str] | None = None


class ProspectCreate(ProspectBase):
    pass


class ProspectPatch(BaseModel):
    business_name: str | None = Field(default=None, min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    location_area: str | None = Field(default=None, max_length=100)
    location_city: str | None = Field(default=None, max_length=100)
    location_state: str | None = Field(default=None, max_length=100)
    location_country: str | None = Field(default=None, max_length=10)
    address: str | None = Field(default=None)
    phone_e164: str | None = Field(default=None, max_length=30)
    phone_raw: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=254)
    website_url: str | None = Field(default=None, max_length=2000)
    social_links: dict[str, str] | None = None


class ProspectOut(ProspectBase):
    id: int
    normalized_name: str | None
    stage: str
    website_status: str
    source_id: int | None
    research_summary: str | None
    last_contacted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    do_not_contact: bool
    do_not_contact_reason: str | None
    do_not_contact_at: datetime | None
    tag_names: list[str] = []

    model_config = {"from_attributes": True}


class DedupeCheckOut(BaseModel):
    exact_match: bool
    exact_prospect_id: int | None
    exact_prospect_name: str | None
    fuzzy_flags: list[int]
    normalized_name: str


class StageTransitionIn(BaseModel):
    stage: str


class StageTransitionOut(BaseModel):
    id: int
    stage: str
    last_contacted_at: datetime | None

    model_config = {"from_attributes": True}


class SuppressionIn(BaseModel):
    reason: str = ""


class SuppressionOut(BaseModel):
    id: int
    stage: str
    do_not_contact: bool
    do_not_contact_reason: str | None

    model_config = {"from_attributes": True}


class ReplyIn(BaseModel):
    text: str
    received_at: datetime | None = None


class ActivityOut(BaseModel):
    id: int
    type: str
    actor: str
    payload: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProfileIn(BaseModel):
    offer: str | None = None
    target_industries: list[str] | None = None
    target_locations: list[str] | None = None
    ideal_signals: list[str] | None = None


class ProfileOut(BaseModel):
    id: int
    offer: str | None
    target_industries: list[str] | None
    target_locations: list[str] | None
    ideal_signals: list[str] | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class ErrorOut(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ResearchRunOut(BaseModel):
    id: int
    prospect_id: int
    status: str
    steps: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    triggered_by: str

    model_config = {"from_attributes": True}


class ResearchStartOut(BaseModel):
    run_id: int
    job_id: int | None
    status: str


class WebsiteCheckOut(BaseModel):
    id: int
    url_checked: str
    outcome: str
    http_status: int | None
    final_url: str | None
    response_ms: int | None
    title: str | None
    has_contact_path: bool
    has_booking_or_cta: bool
    signals: dict[str, Any] | None
    error: str | None
    checked_at: datetime

    model_config = {"from_attributes": True}


class FindingOut(BaseModel):
    id: int
    kind: str
    statement: str
    detail: str | None
    source_url: str | None
    source_type: str
    verification_status: str
    confidence: str
    origin: str
    recorded_at: datetime

    model_config = {"from_attributes": True}


class OutreachMessageOut(BaseModel):
    id: int
    prospect_id: int
    status: str
    active: bool
    current_text: str
    generated_text: str | None
    origin: str
    grounding: list[int] | None
    ungrounded_acknowledgement: bool
    approved_at: datetime | None
    opened_at: datetime | None
    sent_confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageGenerateIn(BaseModel):
    pass


class MessageApproveIn(BaseModel):
    ungrounded_acknowledgement: bool = False


class MessageOpenOut(BaseModel):
    status: str
    wa_me_url: str | None


class MessageConfirmOut(BaseModel):
    status: str
    last_contacted_at: datetime | None


class OpportunityOut(BaseModel):
    id: int
    prospect_id: int
    type: str
    summary: str
    rationale: str | None
    status: str
    origin: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OpportunityCreate(BaseModel):
    type: str = Field(min_length=1, max_length=60)
    summary: str = Field(min_length=1, max_length=1000)
    rationale: str | None = Field(default=None, max_length=4000)


class FollowUpOut(BaseModel):
    id: int
    prospect_id: int
    message_id: int | None
    due_date: datetime | None
    note: str | None
    status: str
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class FollowUpCreate(BaseModel):
    due_date: datetime | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# CSV import (Flow 10)
# ---------------------------------------------------------------------------


class ImportRow(BaseModel):
    index: int
    business_name: str
    category: str | None = None
    location_area: str | None = None
    location_city: str | None = None
    location_state: str | None = None
    location_country: str | None = None
    address: str | None = None
    phone_raw: str | None = None
    email: str | None = None
    website_url: str | None = None
    status: str  # "valid" | "duplicate" | "error"
    reason: str | None = None
    fuzzy: bool = False
    matched_prospect_id: int | None = None
    matched_prospect_name: str | None = None
    selected: bool = True


class ImportPreview(BaseModel):
    import_token: str
    total_rows: int
    valid_count: int
    duplicate_count: int
    error_count: int
    columns_detected: list[str]
    column_mapping: dict[str, str | None]
    rows: list[ImportRow]


class ImportCommitIn(BaseModel):
    import_token: str
    row_indices: list[int]


class ImportResult(BaseModel):
    created: int
    skipped_duplicates: int
    flagged_for_review: int
    errors: int
    created_ids: list[int]


# ---------------------------------------------------------------------------
# Saved views (Phase 6+ free feature)
# ---------------------------------------------------------------------------


class SavedViewBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    filters: dict[str, Any] | None = None


class SavedViewCreate(SavedViewBase):
    pass


class SavedViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    filters: dict[str, Any] | None = None


class SavedViewOut(SavedViewBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Merge (Phase 6+ free feature)
# ---------------------------------------------------------------------------


class DuplicatePairOut(BaseModel):
    source_id: int
    source_name: str
    target_id: int
    target_name: str
    score: float
    reason: str


class MergeIn(BaseModel):
    source_id: int
    target_id: int


# ---------------------------------------------------------------------------
# Retention sweep (Phase 6+ free feature)
# ---------------------------------------------------------------------------


class RetentionSweepOut(BaseModel):
    flagged: int
    already_flagged: int
    skipped: int
    flagged_ids: list[int]
    months: int
    cutoff: str


class RetentionSweepIn(BaseModel):
    months: int = Field(default=12, ge=1, le=60)
