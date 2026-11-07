"""Server-side form validation for HTML forms (SECURITY_AND_PRIVACY.md §1).

HTML forms post raw strings; FastAPI does not run Pydantic on form data, so
every field is re-validated server-side here before touching the service layer
or the database. JSON API bodies keep their Pydantic schemas (schemas.py).

Usage::

    from app.validation import validate_prospect_form
    try:
        data = validate_prospect_form(request_form)
    except ValidationError as exc:
        return templates.TemplateResponse(..., context={"field_errors": exc.fields, ...}, status_code=400)

All limits match docs/SECURITY_AND_PRIVACY.md §1 and docs/DATABASE_DESIGN.md
column sizes. Invalid input is rejected — never silently truncated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Field limits (SECURITY_AND_PRIVACY §1 / DATABASE_DESIGN column sizes)
# ---------------------------------------------------------------------------

BUSINESS_NAME_MAX = 200
CATEGORY_MAX = 100
LOCATION_MAX = 100
COUNTRY_MAX = 10
ADDRESS_MAX = 200
PHONE_MAX = 80
EMAIL_MAX = 254
WEBSITE_MAX = 2000
MESSAGE_MAX = 4096
OPPORTUNITY_TYPE_MAX = 60
OPPORTUNITY_SUMMARY_MAX = 1000
OPPORTUNITY_RATIONALE_MAX = 4000
NOTE_MAX = 1000
OFFER_MAX = 500
SIGNAL_MAX = 100
MAX_SIGNAL_ITEMS = 10

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ValidationError(ValueError):
    """Raised when a form field fails validation.

    ``fields`` maps field name -> human-readable error message.
    """

    def __init__(self, fields: dict[str, str]) -> None:
        self.fields = fields
        super().__init__("; ".join(f"{k}: {v}" for k, v in fields.items()))


def _required(value: str | None, field: str, maximum: int) -> str:
    if value is None or not value.strip():
        raise ValidationError({field: f"{field.replace('_', ' ')} is required"})
    text = value.strip()
    if len(text) > maximum:
        raise ValidationError(
            {field: f"{field.replace('_', ' ')} must be {maximum} characters or fewer"}
        )
    return text


def _optional(value: str | None, field: str, maximum: int) -> str | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if len(text) > maximum:
        raise ValidationError(
            {field: f"{field.replace('_', ' ')} must be {maximum} characters or fewer"}
        )
    return text


def _email(value: str | None, field: str = "email") -> str | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if len(text) > EMAIL_MAX:
        raise ValidationError(
            {field: f"{field.replace('_', ' ')} must be {EMAIL_MAX} characters or fewer"}
        )
    if not _EMAIL_RE.match(text):
        raise ValidationError({field: f"Enter a valid {field.replace('_', ' ')}"})
    return text


def _url(value: str | None, field: str = "website_url") -> str | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if len(text) > WEBSITE_MAX:
        raise ValidationError(
            {field: f"{field.replace('_', ' ')} must be {WEBSITE_MAX} characters or fewer"}
        )
    try:
        parsed = urlparse(text)
    except ValueError as exc:  # pragma: no cover - urlparse rarely raises
        raise ValidationError({field: f"Enter a valid {field.replace('_', ' ')}"}) from exc
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError(
            {field: f"Enter a valid {field.replace('_', ' ')} (http:// or https://)"}
        )
    return text


def _csv_signals(value: str | None, field: str) -> list[str] | None:
    if value is None or not value.strip():
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    if len(items) > MAX_SIGNAL_ITEMS:
        raise ValidationError({field: f"Enter at most {MAX_SIGNAL_ITEMS} items"})
    for item in items:
        if len(item) > SIGNAL_MAX:
            raise ValidationError({field: f"Each item must be {SIGNAL_MAX} characters or fewer"})
    return items


# ---------------------------------------------------------------------------
# Prospect create / update form
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProspectFormData:
    business_name: str
    category: str | None
    location_area: str | None
    location_city: str | None
    location_state: str | None
    location_country: str | None
    address: str | None
    phone_raw: str | None
    email: str | None
    website_url: str | None


def validate_prospect_form(form: dict) -> ProspectFormData:
    """Validate the prospect create / edit HTML form."""
    fields: dict[str, str] = {}
    try:
        business_name = _required(form.get("business_name"), "business_name", BUSINESS_NAME_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        business_name = (form.get("business_name") or "").strip()

    try:
        category = _optional(form.get("category"), "category", CATEGORY_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        category = (form.get("category") or "").strip()[:CATEGORY_MAX]

    try:
        location_area = _optional(form.get("location_area"), "location_area", LOCATION_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        location_area = (form.get("location_area") or "").strip()[:LOCATION_MAX]

    try:
        location_city = _optional(form.get("location_city"), "location_city", LOCATION_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        location_city = (form.get("location_city") or "").strip()[:LOCATION_MAX]

    try:
        location_state = _optional(form.get("location_state"), "location_state", LOCATION_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        location_state = (form.get("location_state") or "").strip()[:LOCATION_MAX]

    try:
        location_country = _optional(form.get("location_country"), "location_country", COUNTRY_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        location_country = (form.get("location_country") or "").strip()[:COUNTRY_MAX]

    try:
        address = _optional(form.get("address"), "address", ADDRESS_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        address = (form.get("address") or "").strip()[:ADDRESS_MAX]

    try:
        phone_raw = _optional(form.get("phone_raw"), "phone_raw", PHONE_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        phone_raw = (form.get("phone_raw") or "").strip()[:PHONE_MAX]

    try:
        email = _email(form.get("email"), "email")
    except ValidationError as exc:
        fields.update(exc.fields)
        email = (form.get("email") or "").strip()[:EMAIL_MAX]

    try:
        website_url = _url(form.get("website_url"), "website_url")
    except ValidationError as exc:
        fields.update(exc.fields)
        website_url = (form.get("website_url") or "").strip()[:WEBSITE_MAX]

    if fields:
        raise ValidationError(fields)

    return ProspectFormData(
        business_name=business_name,
        category=category,
        location_area=location_area,
        location_city=location_city,
        location_state=location_state,
        location_country=location_country,
        address=address,
        phone_raw=phone_raw,
        email=email,
        website_url=website_url,
    )


# ---------------------------------------------------------------------------
# Message (draft) text
# ---------------------------------------------------------------------------


def validate_message_text(value: str | None, field: str = "current_text") -> str:
    """Validate draft / edit message text: required, non-blank, ≤4096."""
    if value is None or not value.strip():
        raise ValidationError({field: "Message text is required"})
    text = value.strip()
    if len(text) > MESSAGE_MAX:
        raise ValidationError({field: f"Message must be {MESSAGE_MAX} characters or fewer"})
    return text


# ---------------------------------------------------------------------------
# Opportunity form
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpportunityFormData:
    type: str
    summary: str
    rationale: str | None


def validate_opportunity_form(form: dict) -> OpportunityFormData:
    """Validate the human-created opportunity form."""
    fields: dict[str, str] = {}
    try:
        opp_type = _required(form.get("type"), "type", OPPORTUNITY_TYPE_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        opp_type = (form.get("type") or "").strip()[:OPPORTUNITY_TYPE_MAX]

    try:
        summary = _required(form.get("summary"), "summary", OPPORTUNITY_SUMMARY_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        summary = (form.get("summary") or "").strip()[:OPPORTUNITY_SUMMARY_MAX]

    try:
        rationale = _optional(form.get("rationale"), "rationale", OPPORTUNITY_RATIONALE_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        rationale = (form.get("rationale") or "").strip()[:OPPORTUNITY_RATIONALE_MAX]

    if fields:
        raise ValidationError(fields)

    return OpportunityFormData(type=opp_type, summary=summary, rationale=rationale)


# ---------------------------------------------------------------------------
# Follow-up form
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FollowUpFormData:
    due_date: str | None  # ISO date string or None
    note: str | None


def validate_follow_up_form(form: dict) -> FollowUpFormData:
    """Validate the follow-up create form."""
    from datetime import date

    fields: dict[str, str] = {}
    raw_due = form.get("due_date")
    due_date: str | None = None
    if raw_due and raw_due.strip():
        try:
            date.fromisoformat(raw_due.strip())
            due_date = raw_due.strip()
        except ValueError:
            fields["due_date"] = "Enter a valid date"

    try:
        note = _optional(form.get("note"), "note", NOTE_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        note = (form.get("note") or "").strip()[:NOTE_MAX]

    if fields:
        raise ValidationError(fields)

    return FollowUpFormData(due_date=due_date, note=note)


# ---------------------------------------------------------------------------
# Profile form
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileFormData:
    offer: str | None
    target_industries: list[str] | None
    target_locations: list[str] | None
    ideal_signals: list[str] | None


def validate_profile_form(form: dict) -> ProfileFormData:
    """Validate the settings → profile form."""
    fields: dict[str, str] = {}
    try:
        offer = _optional(form.get("offer"), "offer", OFFER_MAX)
    except ValidationError as exc:
        fields.update(exc.fields)
        offer = None  # dead code; raise below

    try:
        industries = _csv_signals(form.get("target_industries"), "target_industries")
    except ValidationError as exc:
        fields.update(exc.fields)
        industries = None  # dead code; raise below

    try:
        locations = _csv_signals(form.get("target_locations"), "target_locations")
    except ValidationError as exc:
        fields.update(exc.fields)
        locations = None  # dead code; raise below

    try:
        signals = _csv_signals(form.get("ideal_signals"), "ideal_signals")
    except ValidationError as exc:
        fields.update(exc.fields)
        signals = None  # dead code; raise below

    if fields:
        raise ValidationError(fields)

    return ProfileFormData(
        offer=offer,
        target_industries=industries,
        target_locations=locations,
        ideal_signals=signals,
    )
