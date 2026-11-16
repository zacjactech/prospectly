"""Page routes (HTML). Thin: no domain logic here (AGENTS.md §5)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.cli import perform_backup
from app.config import BASE_DIR, save_settings, settings
from app.db import get_db
from app.jobs.worker import queue_discover_job, queue_research_job
from app.models import (
    Activity,
    DiscoveryJob,
    Finding,
    FollowUp,
    Note,
    OutreachMessage,
    Prospect,
    ResearchRun,
    Source,
    Tag,
    WebsiteCheck,
)
from app.services import dashboard as dashboard_service
from app.services import findings as findings_service
from app.services import import_service, merge_service
from app.services import notes as notes_service
from app.services import opportunity as opportunity_service
from app.services import outreach as outreach_service
from app.services import profile as profile_service
from app.services import prospect as prospect_service
from app.services import providers as providers_service
from app.services import retention as retention_service
from app.services import saved_views as saved_views_service
from app.services.usage import usage_summary
from app.validation import (
    FollowUpFormData,
    OpportunityFormData,
    ProfileFormData,
    ProspectFormData,
    ValidationError,
    validate_follow_up_form,
    validate_message_text,
    validate_opportunity_form,
    validate_profile_form,
    validate_prospect_form,
)
from app.whatsapp import normalize_phone

router = APIRouter(tags=["pages"])

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

DbSession = Annotated[Session, Depends(get_db)]

# ===== Pipeline Canvas board context (spec §2.3) =====
# Terminal states render as ONE merged zone with three sub-badges; everything
# else follows the dashboard's display phases (presentation only — the stage
# order and state machine live in SYSTEM_ARCHITECTURE §3.2 / prospect.py).
TERMINAL_STAGES: tuple[str, ...] = ("not_interested", "do_not_contact", "archived")


def _zone_label(stage: str) -> str:
    return stage.replace("_", " ").title()


def _build_board_bands() -> list[dict]:
    bands: list[dict] = []
    for idx, (label, stages) in enumerate(dashboard_service.PIPELINE_PHASES, start=1):
        bands.append(
            {
                "key": f"{idx:02d}",
                "label": label,
                "zones": [
                    {"key": s, "label": _zone_label(s), "stages": [s], "sub_badges": False}
                    for s in stages
                ],
            }
        )
    bands.append(
        {
            "key": "T",
            "label": "Terminal",
            "zones": [
                {
                    "key": "terminal",
                    "label": "Terminal",
                    "stages": list(TERMINAL_STAGES),
                    "sub_badges": True,
                }
            ],
        }
    )
    return bands


BOARD_BANDS: list[dict] = _build_board_bands()
BOARD_ZONE_KEYS: tuple[str, ...] = tuple(z["key"] for b in BOARD_BANDS for z in b["zones"])

STAGES: tuple[str, ...] = (
    "new",
    "researched",
    "opportunity_identified",
    "outreach_ready",
    "contacted",
    "replied",
    "interested",
    "follow_up_due",
    "converted",
    "not_interested",
    "do_not_contact",
    "archived",
)


def _hx(request: Request) -> bool:
    """Content-negotiation probe (spec §4): HTMX asks for the fragment.

    Same route, same path, same contract — the full-page representation is
    returned unchanged when the HX-Request header is absent (all existing
    tests exercise that path).
    """
    return bool(request.headers.get("hx-request"))


# Removable filter chips (base spec §1.2): label order matches the toolbar.
FILTER_CHIP_LABELS: tuple[tuple[str, str], ...] = (
    ("q", "Search"),
    ("stage", "Stage"),
    ("tag", "Tag"),
    ("category", "Category"),
    ("location", "Location"),
    ("include_archived", "Archived included"),
    ("do_not_contact_only", "Suppressed only"),
)


def _active_filters(
    *,
    stage: str | None,
    tag: str | None,
    category: str | None,
    location: str | None,
    q: str | None,
    include_archived: bool,
    do_not_contact_only: bool,
) -> list[dict[str, str]]:
    """Build the removable filter chips shown under the canvas toolbar.

    Each chip is a plain link to ``/prospects`` with every *other* active
    filter kept, so removal works without JavaScript and cannot drift from
    the URL state (no hidden form state, no fetch endpoint). The URL is the
    single source of truth (spec §V2.2).
    """
    values: dict[str, str | bool | None] = {
        "q": q,
        "stage": stage,
        "tag": tag,
        "category": category,
        "location": location,
        "include_archived": include_archived,
        "do_not_contact_only": do_not_contact_only,
    }
    chips: list[dict[str, str]] = []
    for key, label in FILTER_CHIP_LABELS:
        value = values[key]
        if not value:
            continue
        text = label if isinstance(value, bool) else f"{label}: {value.replace('_', ' ')}"
        remaining = {k: ("true" if v is True else v) for k, v in values.items() if k != key and v}
        url = "/prospects"
        if remaining:
            url += "?" + urlencode(remaining)
        chips.append({"key": key, "label": text, "url": url})
    return chips


def _relative_days(dt: datetime | None) -> str | None:
    """Human "3d"/"today" label for a past timestamp (card meta row)."""
    if dt is None:
        return None
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    days = (now - dt).days
    if days <= 0:
        return "today"
    if days == 1:
        return "1d"
    return f"{days}d"


def _follow_up_rel(due: datetime | date | None, today: date) -> tuple[str, bool]:
    """("overdue 2d" | "due today" | "due 3d", is_due_now) for a follow-up."""
    dd = due.date() if isinstance(due, datetime) else due
    if dd is None:
        return ("", False)
    delta = (dd - today).days
    if delta < 0:
        return (f"overdue {-delta}d", True)
    if delta == 0:
        return ("due today", True)
    return (f"due {delta}d", False)


def _board_context(db: Session, rows: list[Prospect], *, stage: str | None = None) -> dict:
    """Board-only template context: zones, cards, quick-action metadata.

    ``stage`` is the URL stage lens (spec §V2.2): only the matching band/zone
    is rendered, so a shared `?stage=` URL opens the single-zone view. The
    "Show all zones" chip clears the lens.
    """
    bands = BOARD_BANDS
    if stage:
        bands = [
            {**band, "zones": [z for z in band["zones"] if stage in z["stages"]]}
            for band in BOARD_BANDS
            if any(stage in z["stages"] for z in band["zones"])
        ]
    zone_cards: dict[str, list[Prospect]] = {k: [] for k in BOARD_ZONE_KEYS}
    for r in rows:
        key = "terminal" if r.stage in TERMINAL_STAGES else r.stage
        if key in zone_cards:
            zone_cards[key].append(r)

    ids = [r.id for r in rows]
    active_messages: dict[int, OutreachMessage] = {}
    rel_fu: dict[int, str] = {}
    fu_due_ids: set[int] = set()
    if ids:
        for m in (
            db.query(OutreachMessage)
            .filter(
                OutreachMessage.prospect_id.in_(ids),
                OutreachMessage.active.is_(True),
            )
            .all()
        ):
            active_messages[m.prospect_id] = m
        today = datetime.now().date()
        open_followups = (
            db.query(FollowUp)
            .filter(FollowUp.prospect_id.in_(ids), FollowUp.status == "open")
            .order_by(FollowUp.due_date.asc().nullslast())
            .all()
        )
        earliest: dict[int, FollowUp] = {}
        for f in open_followups:
            earliest.setdefault(f.prospect_id, f)
        for pid, f in earliest.items():
            rel, is_due = _follow_up_rel(f.due_date, today)
            if rel:
                rel_fu[pid] = rel
            if is_due:
                fu_due_ids.add(pid)

    rel_contact: dict[int, str] = {
        r.id: label for r in rows if (label := _relative_days(r.last_contacted_at))
    }

    zone_hot: dict[str, str] = {}
    for key, cards in zone_cards.items():
        if any(c.id in fu_due_ids for c in cards):
            zone_hot[key] = "warn"
        elif any(
            active_messages.get(c.id) is not None
            and active_messages[c.id].status == "pending_review"
            for c in cards
        ):
            zone_hot[key] = "accent"

    zone_sub_counts: dict[str, int] = {
        s: sum(1 for c in zone_cards["terminal"] if c.stage == s) for s in TERMINAL_STAGES
    }

    return {
        "bands": bands,
        "zone_cards": zone_cards,
        "zone_hot": zone_hot,
        "zone_sub_counts": zone_sub_counts,
        "active_messages": active_messages,
        "fu_due_ids": fu_due_ids,
        "rel_contact": rel_contact,
        "rel_fu": rel_fu,
    }


def _render_list(
    request: Request,
    db: Session = Depends(get_db),
    *,
    stage: str | None = None,
    tag: str | None = None,
    category: str | None = None,
    location: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
    do_not_contact_only: bool = False,
    message: str | None = None,
    message_type: str = "info",
) -> HTMLResponse:
    rows = prospect_service.list_prospects(
        db,
        stage=stage,
        tag=tag,
        category=category,
        location=location,
        q=q,
        include_archived=include_archived,
        do_not_contact_only=do_not_contact_only,
    )
    filters: dict[str, Any] = {
        "stage": stage,
        "tag": tag,
        "category": category,
        "location": location,
        "q": q,
        "include_archived": include_archived,
        "do_not_contact_only": do_not_contact_only,
    }
    if _hx(request):
        # HTMX board refresh (spec §4): fragment only — flashes + zones.
        return templates.TemplateResponse(
            request=request,
            name="prospects/board_refresh.html",
            context={
                "app_name": settings.app_name,
                **filters,
                "message": message,
                "message_type": message_type,
                **_board_context(db, rows, stage=stage),
            },
        )
    return _render_canvas(
        request,
        db,
        rows=rows,
        message=message,
        message_type=message_type,
        **filters,
    )


# ===== The canvas shell (spec §V2.1/§V2.2) =====
# One screen for the whole app. Every retired page route renders this shell
# with its setup card pre-opened, so direct URLs keep working; HTMX requests
# on the same routes get the panel fragment only.


def _setup_cluster_context(db: Session) -> dict:
    """Live one-line state for each setup card (§V2.3). Read-only queries."""
    profile = profile_service.get_or_create_profile(db)
    needs = dashboard_service.needs_attention(db)
    provider_rows = providers_service.provider_status(db)
    configured = sum(1 for r in provider_rows if r.get("configured"))
    usage = usage_summary(db)
    calls = sum(int(v.get("used") or 0) for k, v in usage.items() if k != "ai")
    views = saved_views_service.list_saved_views(db)
    flagged = retention_service.list_retention_flagged(db)
    # NOTE: do not run find_fuzzy_duplicates() here. It scans every live
    # prospect and made the canvas render O(n) on every page load (~100ms at
    # 5,000 prospects, breaking the Phase 5 budget). The Merge card computes
    # its pairs when opened; the meta line only advertises the surface.
    needs_count = sum(int(i.get("count") or 0) for i in needs)
    industries = len(profile.target_industries or [])
    card_states = {
        "status": {
            "meta": f"{needs_count} need attention" if needs_count else "all clear",
            "warn": needs_count > 0,
        },
        "profile": {
            "meta": f"{'offer set' if profile.offer else 'no offer'} · {industries} industries",
            "warn": not profile.offer,
        },
        "providers": {"meta": f"{configured}/{len(provider_rows)} configured", "warn": False},
        "usage": {"meta": f"{calls} provider calls today", "warn": False},
        "general": {
            "meta": f"{settings.default_follow_up_days}d follow-up · "
            f"{len(settings.category_vocabulary or [])} categories",
            "warn": False,
        },
        "import": {"meta": "CSV → candidates", "warn": False},
        "saved_views": {
            "meta": f"{len(views)} saved view{'s' if len(views) != 1 else ''}",
            "warn": False,
        },
        "retention": {"meta": f"{len(flagged)} flagged", "warn": bool(flagged)},
        "merge": {"meta": "review candidates", "warn": False},
    }
    return {"card_states": card_states, "saved_views": views}


def _outreach_context(db: Session) -> dict:
    """Messages in flight, grouped by state (spec §V2.1 outreach region)."""
    messages = (
        db.query(OutreachMessage)
        .join(Prospect)
        .filter(OutreachMessage.status.in_(("pending_review", "approved", "opened_in_whatsapp")))
        .order_by(OutreachMessage.updated_at.desc())
        .all()
    )
    groups = {
        "pending_review": [m for m in messages if m.status == "pending_review"],
        "approved": [m for m in messages if m.status == "approved"],
        "opened_in_whatsapp": [m for m in messages if m.status == "opened_in_whatsapp"],
    }
    return {
        "outreach_groups": groups,
        "outreach_total": len(messages),
        "outreach_zones": [
            ("pending_review", "Pending review", "awaiting your approval"),
            ("approved", "Approved", "ready to open in WhatsApp"),
            ("opened_in_whatsapp", "Opened", "waiting on your confirmation"),
        ],
    }


def _render_canvas(
    request: Request,
    db: Session,
    *,
    rows: list[Prospect] | None = None,
    open_card: str | None = None,
    panel_body_template: str | None = None,
    panel_context: dict | None = None,
    stage: str | None = None,
    tag: str | None = None,
    category: str | None = None,
    location: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
    do_not_contact_only: bool = False,
    message: str | None = None,
    message_type: str = "info",
    status_code: int = 200,
) -> HTMLResponse:
    if rows is None:
        rows = prospect_service.list_prospects(
            db,
            stage=stage,
            tag=tag,
            category=category,
            location=location,
            q=q,
            include_archived=include_archived,
            do_not_contact_only=do_not_contact_only,
        )
    active_filters = _active_filters(
        stage=stage,
        tag=tag,
        category=category,
        location=location,
        q=q,
        include_archived=include_archived,
        do_not_contact_only=do_not_contact_only,
    )
    filter_params = {
        k: ("true" if v is True else v)
        for k, v in {
            "stage": stage,
            "tag": tag,
            "category": category,
            "location": location,
            "q": q,
            "include_archived": include_archived,
            "do_not_contact_only": do_not_contact_only,
        }.items()
        if v
    }
    filters_href = "/prospects/filters"
    if filter_params:
        filters_href += "?" + urlencode(filter_params)
    # POST → 303 redirects put the confirmation in the query string
    # (e.g. /prospects/6?flash=Message+sent+confirmed). The canvas routes
    # don't take a message parameter, so surface it here; the type is
    # whitelisted because it becomes a CSS class.
    if message is None:
        message = request.query_params.get("flash")
        if message:
            candidate = request.query_params.get("flash_type") or "info"
            message_type = candidate if candidate in ("info", "success", "error") else "info"
    context = {
        "app_name": settings.app_name,
        "canvas_mode": True,
        "prospects": rows,
        "stages": list(STAGES),
        "tags": db.query(Tag).order_by(Tag.name).all(),
        "categories": sorted({r.category for r in rows if r.category}, key=str.lower),
        "current_stage": stage,
        "current_tag": tag,
        "current_category": category,
        "current_location": location,
        "current_q": q,
        "include_archived": include_archived,
        "do_not_contact_only": do_not_contact_only,
        "active_filters": active_filters,
        "filters_href": filters_href,
        "stage_lens_clear_url": next(
            (f["url"] for f in active_filters if f["key"] == "stage"), None
        ),
        "message": message,
        "message_type": message_type,
        "open_card": open_card,
        "panel_body_template": panel_body_template,
        **_board_context(db, rows, stage=stage),
        **_setup_cluster_context(db),
        **_outreach_context(db),
    }
    if panel_context:
        context.update(panel_context)
    return templates.TemplateResponse(
        request=request,
        name="canvas/shell.html",
        context=context,
        status_code=status_code,
    )


def _card_page(
    request: Request,
    db: Session,
    *,
    card: str,
    body: str,
    context: dict[str, object],
    status_code: int = 200,
) -> HTMLResponse:
    """Render a card surface (setup or detail) on the canvas (§V2.2).

    HX-Request → the panel body fragment (no canvas chrome).
    Plain GET  → the canvas shell with that card's panel already open.
    """
    if _hx(request):
        return templates.TemplateResponse(
            request=request,
            name=body,
            context={**context, "panel_mode": True},
            status_code=status_code,
        )
    return _render_canvas(
        request,
        db,
        open_card=card,
        panel_body_template=body,
        panel_context=context,
        status_code=status_code,
    )


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe (no auth, no data)."""
    return {"status": "ok"}


def _status_context(db: Session) -> dict[str, object]:
    """Status card content (was the dashboard page): attention queue, metrics,
    quick start. The 01→03 pipeline strip is dropped — the board itself is on
    screen with phase chips and counts (§V2.3)."""
    return {
        "app_name": settings.app_name,
        "stage_counts": dashboard_service.stage_counts(db),
        "pipeline_phases": dashboard_service.PIPELINE_PHASES,
        "stage_label": dashboard_service.stage_label,
        "total_prospects": dashboard_service.total_prospects(db),
        "metrics": dashboard_service.dashboard_metrics(db),
        "needs_attention": dashboard_service.needs_attention(db),
    }


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: DbSession) -> HTMLResponse:
    """The canvas, with the Status card open (§V2.2)."""
    return _card_page(
        request, db, card="status", body="canvas/cards/status.html", context=_status_context(db)
    )


@router.get("/status", response_class=HTMLResponse)
def status_card(request: Request, db: DbSession) -> HTMLResponse:
    """Status card as its own address (rail item + `g s`)."""
    return _card_page(
        request, db, card="status", body="canvas/cards/status.html", context=_status_context(db)
    )


@router.get("/prospects", response_class=HTMLResponse)
def prospects_list(
    request: Request,
    db: Session = Depends(get_db),
    stage: str | None = None,
    tag: str | None = None,
    category: str | None = None,
    location: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
    do_not_contact_only: bool = False,
) -> HTMLResponse:
    return _render_list(
        request,
        db,
        stage=stage,
        tag=tag,
        category=category,
        location=location,
        q=q,
        include_archived=include_archived,
        do_not_contact_only=do_not_contact_only,
    )


@router.get("/prospects/filters", response_class=HTMLResponse)
def prospects_filters(
    request: Request,
    db: Session = Depends(get_db),
    stage: str | None = None,
    tag: str | None = None,
    category: str | None = None,
    location: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
    do_not_contact_only: bool = False,
) -> HTMLResponse:
    """Filters panel (spec §1.2/§V2.3): a 360px surface, not inline rows.

    The query string carries the current filter state so the panel always
    opens on what the board is showing. HX-Request → panel body fragment.
    """
    categories = sorted(
        (
            row[0]
            for row in db.query(Prospect.category)
            .filter(Prospect.category.isnot(None))
            .distinct()
            .all()
            if row[0]
        ),
        key=str.lower,
    )
    return _card_page(
        request,
        db,
        card="filters",
        body="canvas/cards/filters.html",
        context={
            "app_name": settings.app_name,
            "stages": list(STAGES),
            "tags": db.query(Tag).order_by(Tag.name).all(),
            "categories": categories,
            "current_stage": stage,
            "current_tag": tag,
            "current_category": category,
            "current_location": location,
            "current_q": q,
            "include_archived": include_archived,
            "do_not_contact_only": do_not_contact_only,
            "has_filters": bool(
                stage or tag or category or location or q or include_archived or do_not_contact_only
            ),
            "saved_views": saved_views_service.list_saved_views(db),
        },
    )


@router.get("/prospects/new", response_class=HTMLResponse)
def prospect_new_form(request: Request, db: DbSession) -> HTMLResponse:
    return _card_page(
        request,
        db,
        card="prospect-new",
        body="partials/panel_prospect_form.html",
        context={"app_name": settings.app_name, "prospect": None, "action": "/prospects/new"},
    )


@router.post("/prospects/new", response_class=HTMLResponse, response_model=None)
def prospect_create(
    request: Request,
    db: Session = Depends(get_db),
    business_name: str = Form(...),
    category: str | None = Form(None),
    location_area: str | None = Form(None),
    location_city: str | None = Form(None),
    location_state: str | None = Form(None),
    location_country: str | None = Form(None),
    address: str | None = Form(None),
    phone_raw: str | None = Form(None),
    email: str | None = Form(None),
    website_url: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Create a prospect from the manual add form (Flow 1)."""
    try:
        data: ProspectFormData = validate_prospect_form(
            {
                "business_name": business_name,
                "category": category,
                "location_area": location_area,
                "location_city": location_city,
                "location_state": location_state,
                "location_country": location_country,
                "address": address,
                "phone_raw": phone_raw,
                "email": email,
                "website_url": website_url,
            }
        )
    except ValidationError as exc:
        return _card_page(
            request,
            db,
            card="prospect-new",
            body="partials/panel_prospect_form.html",
            context={
                "app_name": settings.app_name,
                "prospect": None,
                "action": "/prospects/new",
                "field_errors": exc.fields,
                "form": {
                    "business_name": business_name,
                    "category": category,
                    "location_area": location_area,
                    "location_city": location_city,
                    "location_state": location_state,
                    "location_country": location_country,
                    "address": address,
                    "phone_raw": phone_raw,
                    "email": email,
                    "website_url": website_url,
                },
            },
            status_code=400,
        )

    source = db.query(Source).filter(Source.type == "manual").first()
    if source is None:
        source = Source(type="manual", label="Manual add", detail={})
        db.add(source)
        db.flush()

    # Normalize the raw phone to E.164 for dedupe + WhatsApp (D9). Invalid
    # numbers stay in phone_raw only and never produce a wa.me recipient.
    phone_e164 = (
        normalize_phone(data.phone_raw, settings.phone_default_country) if data.phone_raw else None
    )

    prospect, error = prospect_service.create_prospect(
        db,
        business_name=data.business_name,
        source=source,
        category=data.category,
        location_area=data.location_area,
        location_city=data.location_city,
        location_state=data.location_state,
        location_country=data.location_country,
        address=data.address,
        phone_e164=phone_e164,
        phone_raw=data.phone_raw,
        email=data.email,
        website_url=data.website_url,
    )
    if error:
        return _card_page(
            request,
            db,
            card="prospect-new",
            body="partials/panel_prospect_form.html",
            context={
                "app_name": settings.app_name,
                "prospect": None,
                "action": "/prospects/new",
                "error": error,
                "form": {
                    "business_name": business_name,
                    "category": category,
                    "location_area": location_area,
                    "location_city": location_city,
                    "location_state": location_state,
                    "location_country": location_country,
                    "address": address,
                    "phone_raw": phone_raw,
                    "email": email,
                    "website_url": website_url,
                },
            },
            status_code=409,
        )

    assert prospect is not None
    return RedirectResponse(
        url=f"/prospects/{prospect.id}?flash={quote('Prospect created')}&flash_type=success",
        status_code=303,
    )


@router.get("/prospects/{prospect_id}", response_class=HTMLResponse)
def prospect_detail(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return HTMLResponse("Prospect not found", status_code=404)
    activities = (
        db.query(Activity)
        .filter_by(prospect_id=prospect_id)
        .order_by(Activity.created_at.desc())
        .limit(20)
        .all()
    )
    latest_run = (
        db.query(ResearchRun)
        .filter_by(prospect_id=prospect_id)
        .order_by(ResearchRun.created_at.desc())
        .first()
    )
    findings = (
        db.query(Finding)
        .filter_by(prospect_id=prospect_id)
        .order_by(Finding.recorded_at.desc())
        .limit(5)
        .all()
    )
    website_checks = (
        db.query(WebsiteCheck)
        .filter_by(prospect_id=prospect_id)
        .order_by(WebsiteCheck.checked_at.desc())
        .limit(3)
        .all()
    )
    active_message = (
        db.query(OutreachMessage)
        .filter_by(prospect_id=prospect_id, active=True)
        .order_by(OutreachMessage.created_at.desc())
        .first()
    )
    follow_ups = (
        db.query(FollowUp)
        .filter_by(prospect_id=prospect_id, status="open")
        .order_by(FollowUp.due_date.asc().nullslast(), FollowUp.created_at.desc())
        .all()
    )
    notes = db.query(Note).filter_by(prospect_id=prospect_id).order_by(Note.updated_at.desc()).all()
    opportunities = opportunity_service.list_opportunities(db, prospect_id=prospect_id)
    context = {
        "app_name": settings.app_name,
        "prospect": prospect,
        "activities": activities,
        "latest_run": latest_run,
        "findings": findings,
        "website_checks": website_checks,
        "active_message": active_message,
        "follow_ups": follow_ups,
        "notes": notes,
        "opportunities": opportunities,
    }
    if _hx(request):
        # Panel representation (spec §4): the same partial detail.html
        # includes, rendered standalone for #panel-body.
        return templates.TemplateResponse(
            request=request,
            name="partials/panel_detail.html",
            context={**context, "panel_mode": True},
        )
    return _render_canvas(
        request,
        db,
        open_card=f"prospect-{prospect_id}",
        panel_body_template="partials/panel_detail.html",
        panel_context=context,
    )


@router.get("/prospects/{prospect_id}/edit", response_class=HTMLResponse)
def prospect_edit_form(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return HTMLResponse("Prospect not found", status_code=404)
    return _card_page(
        request,
        db,
        card=f"prospect-{prospect_id}-edit",
        body="partials/panel_prospect_form.html",
        context={
            "app_name": settings.app_name,
            "prospect": prospect,
            "action": f"/prospects/{prospect_id}/edit",
        },
    )


@router.post("/prospects/{prospect_id}/edit", response_class=HTMLResponse, response_model=None)
def prospect_update(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
    business_name: str = Form(...),
    category: str | None = Form(None),
    location_area: str | None = Form(None),
    location_city: str | None = Form(None),
    location_state: str | None = Form(None),
    location_country: str | None = Form(None),
    address: str | None = Form(None),
    phone_raw: str | None = Form(None),
    email: str | None = Form(None),
    website_url: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Update prospect fields from the edit form."""
    try:
        data: ProspectFormData = validate_prospect_form(
            {
                "business_name": business_name,
                "category": category,
                "location_area": location_area,
                "location_city": location_city,
                "location_state": location_state,
                "location_country": location_country,
                "address": address,
                "phone_raw": phone_raw,
                "email": email,
                "website_url": website_url,
            }
        )
    except ValidationError as exc:
        return _card_page(
            request,
            db,
            card=f"prospect-{prospect_id}-edit",
            body="partials/panel_prospect_form.html",
            context={
                "app_name": settings.app_name,
                "prospect": db.get(Prospect, prospect_id),
                "action": f"/prospects/{prospect_id}/edit",
                "field_errors": exc.fields,
                "form": {
                    "business_name": business_name,
                    "category": category,
                    "location_area": location_area,
                    "location_city": location_city,
                    "location_state": location_state,
                    "location_country": location_country,
                    "address": address,
                    "phone_raw": phone_raw,
                    "email": email,
                    "website_url": website_url,
                },
            },
            status_code=400,
        )

    prospect, error = prospect_service.update_prospect(
        db,
        prospect_id,
        business_name=data.business_name,
        category=data.category,
        location_area=data.location_area,
        location_city=data.location_city,
        location_state=data.location_state,
        location_country=data.location_country,
        address=data.address,
        phone_e164=normalize_phone(data.phone_raw, settings.phone_default_country)
        if data.phone_raw
        else None,
        phone_raw=data.phone_raw,
        email=data.email,
        website_url=data.website_url,
    )
    if error:
        if error == "Prospect not found":
            return HTMLResponse("Prospect not found", status_code=404)
        return _card_page(
            request,
            db,
            card=f"prospect-{prospect_id}-edit",
            body="partials/panel_prospect_form.html",
            context={
                "app_name": settings.app_name,
                "prospect": db.get(Prospect, prospect_id),
                "action": f"/prospects/{prospect_id}/edit",
                "error": error,
                "form": {
                    "business_name": business_name,
                    "category": category,
                    "location_area": location_area,
                    "location_city": location_city,
                    "location_state": location_state,
                    "location_country": location_country,
                    "address": address,
                    "phone_raw": phone_raw,
                    "email": email,
                    "website_url": website_url,
                },
            },
            status_code=409,
        )
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash={quote('Prospect updated')}&flash_type=success",
        status_code=303,
    )


@router.post("/prospects/{prospect_id}/archive", response_class=HTMLResponse, response_model=None)
def prospect_archive(
    prospect_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    ok = prospect_service.archive_prospect(db, prospect_id)
    if not ok:
        return HTMLResponse("Prospect not found", status_code=404)
    return RedirectResponse(
        url=f"/prospects?flash={quote('Prospect archived')}&flash_type=success",
        status_code=303,
    )


@router.post("/prospects/{prospect_id}/unarchive", response_class=HTMLResponse, response_model=None)
def prospect_unarchive(
    prospect_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    ok = prospect_service.unarchive_prospect(db, prospect_id)
    if not ok:
        prospect = db.get(Prospect, prospect_id)
        if prospect is None:
            return HTMLResponse("Prospect not found", status_code=404)
        return HTMLResponse(
            "Cannot unarchive: a live prospect with the same identity already exists.",
            status_code=409,
        )
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash={quote('Prospect restored')}&flash_type=success",
        status_code=303,
    )


@router.post("/prospects/{prospect_id}/stage", response_class=HTMLResponse, response_model=None)
def prospect_stage(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
    stage: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    prospect, error = prospect_service.transition_stage(db, prospect_id, stage)
    if error:
        if error == "Prospect not found":
            return HTMLResponse("Prospect not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash={quote('Stage updated')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/prospects/{prospect_id}/do-not-contact", response_class=HTMLResponse, response_model=None
)
def prospect_suppress(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
    reason: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    """Suppress a prospect — Flow 8 "Do not contact" button (P10)."""
    prospect, error = prospect_service.set_do_not_contact(
        db, prospect_id, reason=reason.strip(), actor="user"
    )
    if error:
        if error == "Prospect not found":
            return HTMLResponse("Prospect not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash={quote('Prospect suppressed')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/prospects/{prospect_id}/do-not-contact/clear",
    response_class=HTMLResponse,
    response_model=None,
)
def prospect_unsuppress(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
    reason: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    """Clear suppression — Flow 8: explicit action + reason, audit-logged."""
    reason = reason.strip()
    if not reason:
        return HTMLResponse("A reason is required to clear suppression", status_code=400)
    prospect, error = prospect_service.clear_do_not_contact(
        db, prospect_id, reason=reason, actor="user"
    )
    if error:
        if error == "Prospect not found":
            return HTMLResponse("Prospect not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash={quote('Suppression cleared')}&flash_type=success",
        status_code=303,
    )


@router.get("/settings/profile", response_class=HTMLResponse)
def profile_form(request: Request, db: DbSession) -> HTMLResponse:
    profile = profile_service.get_or_create_profile(db)
    discovery_jobs = db.query(DiscoveryJob).order_by(DiscoveryJob.created_at.desc()).limit(5).all()
    return _card_page(
        request,
        db,
        card="profile",
        body="canvas/cards/profile.html",
        context={
            "app_name": settings.app_name,
            "profile": profile,
            "discovery_jobs": discovery_jobs,
        },
    )


@router.post("/settings/profile", response_class=HTMLResponse, response_model=None)
def profile_update(
    request: Request,
    db: Session = Depends(get_db),
    offer: str | None = Form(None),
    target_industries: str | None = Form(None),
    target_locations: str | None = Form(None),
    ideal_signals: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    try:
        data: ProfileFormData = validate_profile_form(
            {
                "offer": offer,
                "target_industries": target_industries,
                "target_locations": target_locations,
                "ideal_signals": ideal_signals,
            }
        )
    except ValidationError as exc:
        return HTMLResponse(
            "; ".join(f"{k}: {v}" for k, v in exc.fields.items()),
            status_code=400,
        )

    profile_service.update_profile(
        db,
        offer=data.offer,
        target_industries=data.target_industries,
        target_locations=data.target_locations,
        ideal_signals=data.ideal_signals,
    )
    return RedirectResponse(
        url=f"/settings/profile?flash={quote('Profile saved')}&flash_type=success",
        status_code=303,
    )


@router.post("/settings/profile/discovery/run", response_class=HTMLResponse, response_model=None)
def profile_discovery_run(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Queue a profile-driven discovery run (Flow 1, SYSTEM_ARCHITECTURE §4.1).

    Creates the ``discovery_job`` row keyed to the profile and the backing
    ``jobs`` row the worker claims; the worker (python -m app.cli worker)
    picks it up — the app itself never runs providers in-request (D5).
    """
    profile = profile_service.get_or_create_profile(db)
    if not (profile.target_industries or profile.target_locations):
        return HTMLResponse(
            "Set target industries or locations in your profile before running discovery.",
            status_code=400,
        )
    discovery_job = DiscoveryJob(profile_id=profile.id, status="queued")
    db.add(discovery_job)
    db.flush()
    queue_discover_job(db, profile_id=profile.id, discovery_job_id=discovery_job.id)
    return RedirectResponse(
        url=(
            f"/settings/profile?flash={quote('Discovery queued — run the worker (python -m app.cli worker) to process it')}"
            "&flash_type=success"
        ),
        status_code=303,
    )


@router.get("/settings/usage", response_class=HTMLResponse)
def usage_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Usage card: provider + AI call accounting per day (§2.7)."""
    stats = dashboard_service.usage_stats(db)
    return _card_page(
        request,
        db,
        card="usage",
        body="canvas/cards/usage.html",
        context={
            "app_name": settings.app_name,
            "providers": stats["providers"],
            "ai": stats["ai"],
            "usage": usage_summary(db),
        },
    )


@router.get("/settings/providers", response_class=HTMLResponse)
def providers_settings_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Providers card: configured/enabled status, usage, caps (§2.7).

    Read-only by design: keys are never rendered, only presence booleans.
    """
    return _card_page(
        request,
        db,
        card="providers",
        body="canvas/cards/providers.html",
        context={
            "app_name": settings.app_name,
            "settings": settings,
            "rows": providers_service.provider_status(db),
        },
    )


@router.get("/settings/general", response_class=HTMLResponse)
def general_settings_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """General card: vocabulary, defaults, caps, backup (§2.7)."""
    return _card_page(
        request,
        db,
        card="general",
        body="canvas/cards/general.html",
        context={
            "app_name": settings.app_name,
            "settings": settings,
            "usage": usage_summary(db),
        },
    )


@router.post("/settings/general", response_class=HTMLResponse, response_model=None)
def general_settings_update(
    request: Request,
    db: Session = Depends(get_db),
    default_follow_up_days: str | None = Form(None),
    category_vocabulary: str | None = Form(None),
    backup: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Update general settings or trigger a database backup."""
    if backup:
        target = perform_backup()
        if target is None:
            return _card_page(
                request,
                db,
                card="general",
                body="canvas/cards/general.html",
                context={
                    "app_name": settings.app_name,
                    "settings": settings,
                    "usage": usage_summary(db),
                    "error": "Backup failed — is the database a SQLite file?",
                },
            )
        return RedirectResponse(
            url=f"/settings/general?flash={quote('Backup created')}&flash_type=success",
            status_code=303,
        )

    days: int | None = None
    if default_follow_up_days is not None and default_follow_up_days.strip():
        try:
            days = int(default_follow_up_days)
        except ValueError:
            return _card_page(
                request,
                db,
                card="general",
                body="canvas/cards/general.html",
                context={
                    "app_name": settings.app_name,
                    "settings": settings,
                    "usage": usage_summary(db),
                    "error": "Default follow-up days must be a number.",
                },
            )

    vocab: list[str] | None = None
    if category_vocabulary is not None:
        vocab = [v.strip() for v in category_vocabulary.split(",") if v.strip()]

    save_settings(
        **{
            k: v
            for k, v in {
                "default_follow_up_days": days,
                "category_vocabulary": vocab,
            }.items()
            if v is not None
        }
    )
    return RedirectResponse(
        url=f"/settings/general?flash={quote('Settings saved')}&flash_type=success",
        status_code=303,
    )


# ===== CSV import (Flow 10) =====


@router.get("/settings/import", response_class=HTMLResponse)
def import_form(request: Request, db: DbSession) -> HTMLResponse:
    """CSV import card, step 1: upload form."""
    return _card_page(
        request,
        db,
        card="import",
        body="canvas/cards/import.html",
        context={
            "app_name": settings.app_name,
            "settings": settings,
            "step": 1,
        },
    )


@router.post("/settings/import", response_class=HTMLResponse, response_model=None)
def import_preview_page(
    request: Request,
    db: Session = Depends(get_db),
    file: UploadFile | None = File(None),
    import_token: str | None = Form(None),
) -> HTMLResponse:
    """CSV import wizard step 2: parse file and show preview (or re-show preview)."""

    # If we already have a token (re-display), load the stashed preview.
    preview = None
    if import_token:
        preview = import_service.load_import_session(import_token)

    if file is not None:
        content = file.file.read()
        try:
            preview = import_service.parse_csv(
                content, default_country=settings.phone_default_country
            )
        except import_service.ImportError as exc:
            # import_service.ImportError subclasses ValueError — catching the
            # builtin here let every structurally invalid CSV escape as a 500.
            return _card_page(
                request,
                db,
                card="import",
                body="canvas/cards/import.html",
                context={
                    "app_name": settings.app_name,
                    "settings": settings,
                    "step": 1,
                    "error": str(exc),
                },
                status_code=400,
            )

    if preview is None:
        return _card_page(
            request,
            db,
            card="import",
            body="canvas/cards/import.html",
            context={
                "app_name": settings.app_name,
                "settings": settings,
                "step": 1,
                "error": "No CSV file provided.",
            },
            status_code=400,
        )

    # Run dedupe against live DB so the preview flags duplicates.
    _, dedupe_counts = import_service.assess_duplicates(
        db, preview["rows"], default_country=settings.phone_default_country
    )
    preview["valid_count"] = dedupe_counts["valid"]
    preview["duplicate_count"] = dedupe_counts["duplicate"]

    # Stash parsed (+deduped) data for the commit step.
    session_token = hashlib.sha256(os.urandom(16)).hexdigest()[:24]
    import_service.save_import_session(session_token, preview)

    rows = []
    for r in preview["rows"]:
        rows.append(
            {
                "index": r["index"],
                "business_name": r["fields"].get("business_name", ""),
                "category": r["fields"].get("category") or "",
                "location_city": r["fields"].get("location_city") or "",
                "phone_raw": r["fields"].get("phone_raw") or "",
                "phone_e164": r["fields"].get("phone_e164") or "",
                "email": r["fields"].get("email") or "",
                "website_url": r["fields"].get("website_url") or "",
                "status": r["status"],
                "reason": r["reason"] or "",
                "fuzzy": r["fuzzy"],
                "matched_prospect_name": r["matched_prospect_name"] or "",
                "selected": True,
            }
        )

    return _card_page(
        request,
        db,
        card="import",
        body="canvas/cards/import.html",
        context={
            "app_name": settings.app_name,
            "step": 2,
            "import_token": session_token,
            "total_rows": preview["total_rows"],
            "valid_count": preview["valid_count"],
            "duplicate_count": preview["duplicate_count"],
            "error_count": preview["error_count"],
            "columns_detected": preview["columns_detected"],
            "column_mapping": preview["column_mapping"],
            "rows": rows,
        },
    )


@router.post("/settings/import/commit", response_class=HTMLResponse, response_model=None)
def import_commit_page(
    request: Request,
    db: Session = Depends(get_db),
    import_token: str = Form(...),
    row_indices: list[int] = Form([]),
) -> HTMLResponse:
    """CSV import wizard step 3: commit selected rows and show results."""
    preview = import_service.load_import_session(import_token)
    if preview is None:
        return _card_page(
            request,
            db,
            card="import",
            body="canvas/cards/import.html",
            context={
                "app_name": settings.app_name,
                "settings": settings,
                "step": 1,
                "error": "Import session expired or not found. Please re-upload.",
            },
            status_code=404,
        )

    result = import_service.commit_import(
        db,
        row_indices=row_indices,
        all_rows=preview["rows"],
        default_country=settings.phone_default_country,
    )

    import_service.delete_import_session(import_token)

    return _card_page(
        request,
        db,
        card="import",
        body="canvas/cards/import.html",
        context={
            "app_name": settings.app_name,
            "step": 3,
            "result": result,
            "total_rows": preview["total_rows"],
        },
    )


# ===== Saved views (Phase 6+ free feature) =====


@router.get("/settings/saved-views", response_class=HTMLResponse)
def saved_views_list(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Saved views list page."""
    views = saved_views_service.list_saved_views(db)
    return _card_page(
        request,
        db,
        card="saved_views",
        body="canvas/cards/saved_views.html",
        context={
            "app_name": settings.app_name,
            "settings": settings,
            "views": views,
        },
    )


@router.post("/settings/saved-views", response_class=HTMLResponse, response_model=None)
def saved_views_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    filters: str | None = Form(None),
    stage: str | None = Form(None),
    tag: str | None = Form(None),
    category: str | None = Form(None),
    location: str | None = Form(None),
    q: str | None = Form(None),
    include_archived: str | None = Form(None),
    do_not_contact_only: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Create a saved view from the HTML form.

    Accepts either a JSON ``filters`` blob or the structured fields the
    Prospects list posts via "Save current view" — both are validated
    against the list's whitelisted query keys (M3).
    """
    filters_dict: dict | None = None
    if filters and filters.strip():
        try:
            parsed = json.loads(filters)
        except json.JSONDecodeError:
            return HTMLResponse("Invalid JSON in filters field.", status_code=400)
        if not isinstance(parsed, dict):
            return HTMLResponse("filters must be a JSON object.", status_code=400)
        unknown = saved_views_service.unknown_filter_keys(parsed)
        if unknown:
            return HTMLResponse("Unknown filter key(s): " + ", ".join(unknown), status_code=400)
        filters_dict = parsed

    structured = {
        k: v
        for k, v in {
            "stage": stage,
            "tag": tag,
            "category": category,
            "location": location,
            "q": q,
            "include_archived": include_archived,
            "do_not_contact_only": do_not_contact_only,
        }.items()
        if v
    }
    if structured:
        filters_dict = {**(filters_dict or {}), **structured}

    saved_views_service.create_saved_view(db, name=name, filters=filters_dict)
    return RedirectResponse(
        url=f"/settings/saved-views?flash={quote('View saved')}&flash_type=success",
        status_code=303,
    )


@router.get("/prospects/views/{view_id}/apply", response_class=HTMLResponse, response_model=None)
def saved_view_apply(
    request: Request,
    view_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Apply a saved view: redirect to the Prospects list with its filters."""
    view = saved_views_service.get_saved_view(db, view_id)
    if view is None:
        return RedirectResponse(
            url="/settings/saved-views?flash="
            + quote("Saved view not found")
            + "&flash_type=error",
            status_code=303,
        )
    clean = saved_views_service.sanitize_filters(view.filters) or {}
    params = urlencode(
        {
            k: ("true" if v is True else str(v))
            for k, v in clean.items()
            if v not in (None, "", False)
        }
    )
    target = f"/prospects?{params}" if params else "/prospects"
    return RedirectResponse(url=target, status_code=303)


@router.post(
    "/settings/saved-views/{view_id}/delete", response_class=HTMLResponse, response_model=None
)
def saved_views_delete(
    request: Request,
    view_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Delete a saved view."""
    ok = saved_views_service.delete_saved_view(db, view_id=view_id)
    if not ok:
        return HTMLResponse("Saved view not found", status_code=404)
    return RedirectResponse(
        url=f"/settings/saved-views?flash={quote('View deleted')}&flash_type=success",
        status_code=303,
    )


# ===== Merge (Phase 6+ free feature) =====


@router.get("/merge/duplicates", response_class=HTMLResponse)
def merge_duplicates_list(
    request: Request,
    db: Session = Depends(get_db),
    min_score: float = 0.85,
) -> HTMLResponse:
    """Fuzzy duplicate review page."""
    pairs = merge_service.find_fuzzy_duplicates(db, min_score=min_score)
    return _card_page(
        request,
        db,
        card="merge",
        body="canvas/cards/merge.html",
        context={
            "app_name": settings.app_name,
            "settings": settings,
            "pairs": pairs,
            "min_score": min_score,
        },
    )


@router.post("/merge", response_class=HTMLResponse, response_model=None)
def merge_prospects_page(
    request: Request,
    db: Session = Depends(get_db),
    source_id: int = Form(...),
    target_id: int = Form(...),
) -> HTMLResponse | RedirectResponse:
    """Merge source prospect into target prospect from HTML form."""
    target, error = merge_service.merge_prospects(
        db, source_id=source_id, target_id=target_id, actor="user"
    )
    if error:
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/merge/duplicates?flash={quote('Prospects merged')}&flash_type=success",
        status_code=303,
    )


# ===== Retention sweep (Phase 6+ free feature) =====


@router.get("/settings/retention", response_class=HTMLResponse)
def retention_sweep_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Retention sweep page - flag inactive prospects for review."""
    flagged = retention_service.list_retention_flagged(db)
    return _card_page(
        request,
        db,
        card="retention",
        body="canvas/cards/retention.html",
        context={
            "app_name": settings.app_name,
            "settings": settings,
            "flagged": flagged,
        },
    )


@router.post("/settings/retention/sweep", response_class=HTMLResponse, response_model=None)
def retention_sweep_run(
    request: Request,
    db: Session = Depends(get_db),
    months: int = Form(12),
) -> HTMLResponse | RedirectResponse:
    """Run retention sweep from HTML form."""
    result = retention_service.sweep_inactive_prospects(db, months=months, actor="user")
    flash_msg = f"Sweep complete: {result['flagged']} flagged"
    return RedirectResponse(
        url=f"/settings/retention?flash={quote(flash_msg)}&flash_type=success",
        status_code=303,
    )


@router.post("/retention/{prospect_id}/clear", response_class=HTMLResponse, response_model=None)
def retention_clear_flag(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Clear retention-review flag from a prospect."""
    ok = retention_service.clear_retention_flag(db, prospect_id=prospect_id, actor="user")
    if not ok:
        return HTMLResponse("Prospect not found or not flagged", status_code=404)
    return RedirectResponse(
        url=f"/settings/retention?flash={quote('Retention flag cleared')}&flash_type=success",
        status_code=303,
    )


@router.get("/prospects/{prospect_id}/research", response_class=HTMLResponse)
def prospect_research(
    request: Request,
    prospect_id: int,
    db: DbSession,
) -> HTMLResponse:
    """Research page for a prospect - shows findings, website checks, and run history."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return HTMLResponse("Prospect not found", status_code=404)

    runs = (
        db.query(ResearchRun)
        .filter_by(prospect_id=prospect_id)
        .order_by(ResearchRun.created_at.desc())
        .all()
    )
    latest_run = runs[0] if runs else None

    findings = (
        db.query(Finding)
        .filter_by(prospect_id=prospect_id)
        .order_by(Finding.recorded_at.desc())
        .all()
    )
    website_checks = (
        db.query(WebsiteCheck)
        .filter_by(prospect_id=prospect_id)
        .order_by(WebsiteCheck.checked_at.desc())
        .all()
    )

    return _card_page(
        request,
        db,
        card=f"research-{prospect_id}",
        body="partials/panel_research.html",
        context={
            "app_name": settings.app_name,
            "prospect": prospect,
            "runs": runs,
            "latest_run": latest_run,
            "findings": findings,
            "website_checks": website_checks,
        },
    )


@router.get("/prospects/{prospect_id}/research/status", response_class=HTMLResponse)
def prospect_research_status(
    request: Request,
    prospect_id: int,
    db: DbSession,
) -> HTMLResponse:
    """HTMX partial: live research progress + findings (SYSTEM_ARCHITECTURE §4.2).

    Polled by the research page while a run is ``running``. A terminal run
    renders the fragment without the ``hx-*`` attributes, so the outerHTML swap
    replaces the polling node and polling stops by itself. Findings are part of
    the fragment so they refresh on the same swap.
    """
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return HTMLResponse("Prospect not found", status_code=404)

    latest_run = (
        db.query(ResearchRun)
        .filter_by(prospect_id=prospect_id)
        .order_by(ResearchRun.created_at.desc())
        .first()
    )
    findings = (
        db.query(Finding)
        .filter_by(prospect_id=prospect_id)
        .order_by(Finding.recorded_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/research_live.html",
        context={
            "prospect": prospect,
            "latest_run": latest_run,
            "findings": findings,
        },
    )


@router.post(
    "/prospects/{prospect_id}/research/start", response_class=HTMLResponse, response_model=None
)
def prospect_research_start(
    request: Request,
    prospect_id: int,
    db: DbSession,
) -> HTMLResponse | RedirectResponse:
    """Start a research run for a prospect."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return HTMLResponse("Prospect not found", status_code=404)

    run = ResearchRun(prospect_id=prospect_id, triggered_by="user")
    db.add(run)
    db.flush()

    # Queue a background job instead of running research synchronously.
    queue_research_job(db, prospect_id=prospect_id, research_run_id=run.id)

    return RedirectResponse(url=f"/prospects/{prospect_id}/research", status_code=303)


# ===== Findings evidence CRUD (USER_FLOWS P8, UI spec §2.3) =====


def _finding_error_response(error: str) -> HTMLResponse:
    """Map a findings-service error to the houses status set (404/409/400)."""
    if error in ("Prospect not found", "Finding not found"):
        return HTMLResponse(error, status_code=404)
    if error in (findings_service.DUPLICATE_ERROR, findings_service.LINKED_ERROR):
        return HTMLResponse(error, status_code=409)
    return HTMLResponse(error, status_code=400)


@router.post(
    "/prospects/{prospect_id}/findings",
    response_class=HTMLResponse,
    response_model=None,
)
def finding_create_page(
    prospect_id: int,
    db: Session = Depends(get_db),
    kind: str = Form(...),
    statement: str = Form(...),
    detail: str | None = Form(None),
    source_url: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Add a manually entered finding (forced human/unverified/low, P5)."""
    finding, error = findings_service.create_finding(
        db,
        prospect_id=prospect_id,
        kind=kind,
        statement=statement,
        detail=detail,
        source_url=source_url,
        actor="user",
    )
    if error:
        return _finding_error_response(error)
    assert finding is not None
    return RedirectResponse(
        url=(
            f"/prospects/{prospect_id}/research?flash={quote('Finding added')}&flash_type=success"
        ),
        status_code=303,
    )


@router.post("/findings/{finding_id}/edit", response_class=HTMLResponse, response_model=None)
def finding_edit_page(
    finding_id: int,
    db: Session = Depends(get_db),
    statement: str = Form(...),
    detail: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Edit a finding's statement/detail (provenance preserved, §2.6)."""
    finding, error = findings_service.edit_finding(
        db, finding_id=finding_id, statement=statement, detail=detail, actor="user"
    )
    if error:
        return _finding_error_response(error)
    assert finding is not None
    return RedirectResponse(
        url=(
            f"/prospects/{finding.prospect_id}/research"
            f"?flash={quote('Finding updated')}&flash_type=success"
        ),
        status_code=303,
    )


@router.post("/findings/{finding_id}/verify", response_class=HTMLResponse, response_model=None)
def finding_verify_page(
    finding_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Verify a finding — a human action that may raise the status (P5)."""
    finding, error = findings_service.verify_finding(db, finding_id=finding_id, actor="user")
    if error:
        return _finding_error_response(error)
    assert finding is not None
    return RedirectResponse(
        url=(
            f"/prospects/{finding.prospect_id}/research"
            f"?flash={quote('Finding verified')}&flash_type=success"
        ),
        status_code=303,
    )


@router.post("/findings/{finding_id}/delete", response_class=HTMLResponse, response_model=None)
def finding_delete_page(
    finding_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Hard-delete a finding unless an opportunity depends on it."""
    finding, error = findings_service.delete_finding(db, finding_id=finding_id, actor="user")
    if error:
        return _finding_error_response(error)
    assert finding is not None
    return RedirectResponse(
        url=(
            f"/prospects/{finding.prospect_id}/research"
            f"?flash={quote('Finding deleted')}&flash_type=success"
        ),
        status_code=303,
    )


# ===== Notes (Flow 9 handoff note, UI spec §2.3) =====


@router.post("/prospects/{prospect_id}/notes", response_class=HTMLResponse, response_model=None)
def note_create_page(
    prospect_id: int,
    db: Session = Depends(get_db),
    body: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    """Add a note from the prospect detail page."""
    note, error = notes_service.add_note(db, prospect_id=prospect_id, body=body, actor="user")
    if error:
        if error == "Prospect not found":
            return HTMLResponse(error, status_code=404)
        return HTMLResponse(error, status_code=400)
    assert note is not None
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash={quote('Note added')}&flash_type=success",
        status_code=303,
    )


@router.post("/notes/{note_id}/edit", response_class=HTMLResponse, response_model=None)
def note_edit_page(
    note_id: int,
    db: Session = Depends(get_db),
    body: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    """Edit a note from the prospect detail page."""
    note, error = notes_service.edit_note(db, note_id=note_id, body=body, actor="user")
    if error:
        if error == "Note not found":
            return HTMLResponse(error, status_code=404)
        return HTMLResponse(error, status_code=400)
    assert note is not None
    return RedirectResponse(
        url=f"/prospects/{note.prospect_id}?flash={quote('Note updated')}&flash_type=success",
        status_code=303,
    )


# ===== Opportunities (Flow 4) =====


@router.post(
    "/prospects/{prospect_id}/opportunities",
    response_class=HTMLResponse,
    response_model=None,
)
def opportunity_create(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
    type: str = Form(...),
    summary: str = Form(...),
    rationale: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Human-created opportunity (Flow 4, origin='human' path)."""
    try:
        data: OpportunityFormData = validate_opportunity_form(
            {"type": type, "summary": summary, "rationale": rationale}
        )
    except ValidationError as exc:
        return HTMLResponse(
            "; ".join(f"{k}: {v}" for k, v in exc.fields.items()),
            status_code=400,
        )

    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return HTMLResponse("Prospect not found", status_code=404)
    opportunity, error = opportunity_service.create_opportunity(
        db,
        prospect_id=prospect_id,
        type=data.type,
        summary=data.summary,
        rationale=data.rationale,
        origin="human",
        actor="user",
    )
    if error:
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash={quote('Opportunity added')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/opportunities/{opportunity_id}/validate",
    response_class=HTMLResponse,
    response_model=None,
)
def opportunity_validate(
    request: Request,
    opportunity_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Validate a proposed opportunity (operator decision, Flow 4)."""
    opportunity, error = opportunity_service.validate_opportunity(
        db, opportunity_id=opportunity_id, actor="user"
    )
    if error:
        if error == "Opportunity not found":
            return HTMLResponse("Opportunity not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    assert opportunity is not None
    return RedirectResponse(
        url=f"/prospects/{opportunity.prospect_id}?flash={quote('Opportunity validated')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/opportunities/{opportunity_id}/dismiss",
    response_class=HTMLResponse,
    response_model=None,
)
def opportunity_dismiss(
    request: Request,
    opportunity_id: int,
    db: Session = Depends(get_db),
    reason: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Dismiss a proposed opportunity (operator decision, Flow 4)."""
    opportunity, error = opportunity_service.dismiss_opportunity(
        db, opportunity_id=opportunity_id, reason=reason, actor="user"
    )
    if error:
        if error == "Opportunity not found":
            return HTMLResponse("Opportunity not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    assert opportunity is not None
    return RedirectResponse(
        url=f"/prospects/{opportunity.prospect_id}?flash={quote('Opportunity dismissed')}&flash_type=success",
        status_code=303,
    )


# ===== Outreach messages (Flow 5) =====


@router.post(
    "/prospects/{prospect_id}/draft/generate",
    response_class=HTMLResponse,
    response_model=None,
)
def draft_generate(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Generate a draft from research findings (Flow 5)."""
    message, error = outreach_service.generate_draft(db, prospect_id=prospect_id, actor="user")
    if error:
        if error == "Prospect not found":
            return HTMLResponse("Prospect not found", status_code=404)
        if error.startswith("suppressed:"):
            return HTMLResponse(error, status_code=403)
        if error.startswith("cap:"):
            return HTMLResponse(error.removeprefix("cap: "), status_code=429)
        if error.startswith("ai:"):
            return HTMLResponse(error.removeprefix("ai: "), status_code=502)
        return HTMLResponse(error, status_code=409)
    assert message is not None
    return RedirectResponse(
        url=f"/messages/{message.id}/edit?flash={quote('Draft generated')}&flash_type=success",
        status_code=303,
    )


@router.get("/messages/{message_id}/edit", response_class=HTMLResponse)
def message_edit_form(
    request: Request,
    message_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Draft editor + grounding panel + gated action chain (Flow 5)."""
    message = db.get(OutreachMessage, message_id)
    if message is None:
        return HTMLResponse("Message not found", status_code=404)
    prospect = db.get(Prospect, message.prospect_id)
    if prospect is None:
        return HTMLResponse("Prospect not found", status_code=404)
    findings = (
        db.query(Finding)
        .filter_by(prospect_id=message.prospect_id)
        .order_by(Finding.recorded_at.desc())
        .all()
    )
    grounded_ids = set(message.grounding or [])
    grounded_findings = [f for f in findings if f.id in grounded_ids]
    context = {
        "app_name": settings.app_name,
        "prospect": prospect,
        "message": message,
        "findings": findings,
        "grounded_findings": grounded_findings,
    }
    if _hx(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/panel_message.html",
            context={**context, "panel_mode": True},
        )
    return _render_canvas(
        request,
        db,
        open_card=f"message-{message_id}",
        panel_body_template="partials/panel_message.html",
        panel_context=context,
    )


@router.post(
    "/messages/{message_id}/edit",
    response_class=HTMLResponse,
    response_model=None,
)
def message_edit(
    request: Request,
    message_id: int,
    db: Session = Depends(get_db),
    current_text: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    """Edit draft text — any edit demotes the message back to draft (§3.1)."""
    try:
        text = validate_message_text(current_text, field="current_text")
    except ValidationError as exc:
        message = db.get(OutreachMessage, message_id)
        if message is None:
            return HTMLResponse("Message not found", status_code=404)
        prospect = db.get(Prospect, message.prospect_id)
        if prospect is None:
            return HTMLResponse("Prospect not found", status_code=404)
        findings = (
            db.query(Finding)
            .filter_by(prospect_id=message.prospect_id)
            .order_by(Finding.recorded_at.desc())
            .all()
        )
        grounded_ids = set(message.grounding or [])
        grounded_findings = [f for f in findings if f.id in grounded_ids]
        context = {
            "app_name": settings.app_name,
            "prospect": prospect,
            "message": message,
            "findings": findings,
            "grounded_findings": grounded_findings,
            "field_errors": exc.fields,
            "form": {"current_text": current_text},
        }
        # Panel validation re-render (spec §4: "422 re-render lands in panel"):
        # same route and 400 contract; a plain POST gets the canvas with this
        # message's panel open, an HTMX POST gets the fragment.
        return _card_page(
            request,
            db,
            card=f"message-{message_id}",
            body="partials/panel_message.html",
            context=context,
            status_code=400,
        )

    message, error = outreach_service.edit_message(
        db, message_id=message_id, current_text=text, actor="user"
    )
    if error:
        if error == "Message not found":
            return HTMLResponse("Message not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/messages/{message_id}/edit?flash={quote('Draft saved')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/messages/{message_id}/submit",
    response_class=HTMLResponse,
    response_model=None,
)
def message_submit(
    request: Request,
    message_id: int,
    db: Session = Depends(get_db),
    current_text: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    """Submit a draft for review: draft → pending_review."""
    try:
        text = validate_message_text(current_text, field="current_text")
    except ValidationError as exc:
        message = db.get(OutreachMessage, message_id)
        if message is None:
            return HTMLResponse("Message not found", status_code=404)
        prospect = db.get(Prospect, message.prospect_id)
        if prospect is None:
            return HTMLResponse("Prospect not found", status_code=404)
        findings = (
            db.query(Finding)
            .filter_by(prospect_id=message.prospect_id)
            .order_by(Finding.recorded_at.desc())
            .all()
        )
        grounded_ids = set(message.grounding or [])
        grounded_findings = [f for f in findings if f.id in grounded_ids]
        context = {
            "app_name": settings.app_name,
            "prospect": prospect,
            "message": message,
            "findings": findings,
            "grounded_findings": grounded_findings,
            "field_errors": exc.fields,
            "form": {"current_text": current_text},
        }
        return _card_page(
            request,
            db,
            card=f"message-{message_id}",
            body="partials/panel_message.html",
            context=context,
            status_code=400,
        )

    message, error = outreach_service.submit_message(
        db, message_id=message_id, current_text=text, actor="user"
    )
    if error:
        if error == "Message not found":
            return HTMLResponse("Message not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/messages/{message_id}/edit?flash={quote('Submitted for review')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/messages/{message_id}/approve",
    response_class=HTMLResponse,
    response_model=None,
)
def message_approve(
    request: Request,
    message_id: int,
    db: Session = Depends(get_db),
    ungrounded_acknowledgement: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Approve for sending: pending_review → approved (guards applied)."""
    message, error = outreach_service.approve_message(
        db,
        message_id=message_id,
        ungrounded_acknowledgement=bool(ungrounded_acknowledgement),
        actor="user",
    )
    if error:
        if error == "Message not found":
            return HTMLResponse("Message not found", status_code=404)
        if error.startswith("suppressed:"):
            return HTMLResponse(error, status_code=403)
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/messages/{message_id}/edit?flash={quote('Approved')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/messages/{message_id}/open",
    response_class=HTMLResponse,
    response_model=None,
)
def message_open(
    request: Request,
    message_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Open in WhatsApp: approved → opened_in_whatsapp, returns wa.me URL."""
    message, error, wa_me_url = outreach_service.open_message(
        db, message_id=message_id, actor="user"
    )
    if error:
        if error == "Message not found":
            return HTMLResponse("Message not found", status_code=404)
        if error.startswith("suppressed:"):
            return HTMLResponse(error, status_code=403)
        return HTMLResponse(error, status_code=409)
    assert message is not None
    prospect = db.get(Prospect, message.prospect_id)
    # Full-page POST (no hx-*): answer with the canvas + this panel open (§V2.2).
    return _card_page(
        request,
        db,
        card=f"message-{message_id}-open",
        body="partials/panel_message_open.html",
        context={
            "app_name": settings.app_name,
            "prospect": prospect,
            "message": message,
            "wa_me_url": wa_me_url,
        },
    )


@router.post(
    "/messages/{message_id}/confirm-sent",
    response_class=HTMLResponse,
    response_model=None,
)
def message_confirm_sent(
    request: Request,
    message_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Confirm the user sent the message: opened_in_whatsapp → sent_confirmed."""
    message, error = outreach_service.confirm_sent(db, message_id=message_id, actor="user")
    if error:
        if error == "Message not found":
            return HTMLResponse("Message not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    assert message is not None
    return RedirectResponse(
        url=f"/prospects/{message.prospect_id}?flash={quote('Message sent confirmed')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/messages/{message_id}/not-sent",
    response_class=HTMLResponse,
    response_model=None,
)
def message_not_sent(
    request: Request,
    message_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """User did not send: opened_in_whatsapp → approved (ready to re-open)."""
    message, error = outreach_service.not_sent(db, message_id=message_id, actor="user")
    if error:
        if error == "Message not found":
            return HTMLResponse("Message not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/messages/{message_id}/edit?flash={quote('Returned to approved')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/prospects/{prospect_id}/start-new-outreach",
    response_class=HTMLResponse,
    response_model=None,
)
def start_new_outreach(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Start a new draft after a message is sent_confirmed (Flow 7 guard)."""
    message, error = outreach_service.start_new_outreach(db, prospect_id=prospect_id, actor="user")
    if error:
        if error == "Prospect not found":
            return HTMLResponse("Prospect not found", status_code=404)
        if error.startswith("suppressed:"):
            return HTMLResponse(error, status_code=403)
        return HTMLResponse(error, status_code=409)
    assert message is not None
    return RedirectResponse(
        url=f"/messages/{message.id}/edit?flash={quote('New outreach started')}&flash_type=success",
        status_code=303,
    )


# ===== Flow 6: Reply =====


@router.post(
    "/prospects/{prospect_id}/reply",
    response_class=HTMLResponse,
    response_model=None,
)
def prospect_reply(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
    text: str = Form(...),
    received_at: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    """Record an inbound WhatsApp reply: stage → replied (Flow 6)."""
    parsed = None
    if received_at:
        try:
            parsed = datetime.fromisoformat(received_at)
        except ValueError:
            parsed = None
    prospect, error = outreach_service.record_reply(
        db, prospect_id=prospect_id, text=text, received_at=parsed, actor="user"
    )
    if error:
        if error == "Prospect not found":
            return HTMLResponse("Prospect not found", status_code=404)
        if error.startswith("suppressed:"):
            return HTMLResponse(error, status_code=403)
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash={quote('Reply recorded')}&flash_type=success",
        status_code=303,
    )


# ===== Flow 7: Follow-ups =====


@router.post(
    "/prospects/{prospect_id}/follow-ups",
    response_class=HTMLResponse,
    response_model=None,
)
def follow_up_create(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
    due_date: str | None = Form(None),
    note: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    try:
        data: FollowUpFormData = validate_follow_up_form({"due_date": due_date, "note": note})
    except ValidationError as exc:
        return HTMLResponse(
            "; ".join(f"{k}: {v}" for k, v in exc.fields.items()),
            status_code=400,
        )

    parsed_due = None
    if data.due_date:
        parsed_due = datetime.fromisoformat(data.due_date)
    follow_up, error = outreach_service.create_follow_up(
        db, prospect_id=prospect_id, due_date=parsed_due, note=data.note, actor="user"
    )
    if error:
        if error == "Prospect not found":
            return HTMLResponse("Prospect not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash={quote('Follow-up added')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/follow-ups/{follow_up_id}/complete",
    response_class=HTMLResponse,
    response_model=None,
)
def follow_up_complete(
    request: Request,
    follow_up_id: int,
    db: Session = Depends(get_db),
    note: str | None = Form(None),
) -> HTMLResponse | RedirectResponse:
    follow_up, error = outreach_service.complete_follow_up(
        db, follow_up_id=follow_up_id, note=note, actor="user"
    )
    if error:
        if error == "Follow-up not found":
            return HTMLResponse("Follow-up not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    assert follow_up is not None
    return RedirectResponse(
        url=f"/prospects/{follow_up.prospect_id}?flash={quote('Follow-up completed')}&flash_type=success",
        status_code=303,
    )


@router.post(
    "/follow-ups/{follow_up_id}/cancel",
    response_class=HTMLResponse,
    response_model=None,
)
def follow_up_cancel(
    request: Request,
    follow_up_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    follow_up, error = outreach_service.cancel_follow_up(
        db, follow_up_id=follow_up_id, actor="user"
    )
    if error:
        if error == "Follow-up not found":
            return HTMLResponse("Follow-up not found", status_code=404)
        return HTMLResponse(error, status_code=409)
    assert follow_up is not None
    return RedirectResponse(
        url=f"/prospects/{follow_up.prospect_id}?flash={quote('Follow-up cancelled')}&flash_type=success",
        status_code=303,
    )


# ===== Outreach queue (Flow 5 / §2.4) =====


@router.get("/outreach", response_class=HTMLResponse)
def outreach_queue(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """The canvas, framed on the outreach region (messages in flight).

    HX-Request → the outreach region fragment only, so panel actions can
    refresh the region without a page reload (spec §V2.1/§3.4).
    """
    if _hx(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/canvas_outreach_region.html",
            context=_outreach_context(db),
        )
    return _render_canvas(request, db)
