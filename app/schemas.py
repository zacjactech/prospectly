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
