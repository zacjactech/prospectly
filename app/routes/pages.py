"""Page routes (HTML). Thin: no domain logic here (AGENTS.md §5)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.cli import perform_backup
from app.config import BASE_DIR, save_settings, settings
from app.db import get_db
from app.jobs.worker import queue_research_job
from app.models import (
    Activity,
    Finding,
    FollowUp,
    OutreachMessage,
    Prospect,
    ResearchRun,
    Source,
    Tag,
    WebsiteCheck,
)
from app.services import dashboard as dashboard_service
from app.services import import_service, merge_service
from app.services import opportunity as opportunity_service
from app.services import outreach as outreach_service
from app.services import profile as profile_service
from app.services import prospect as prospect_service
from app.services import retention as retention_service
from app.services import saved_views as saved_views_service
from app.services.activity import log_activity
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
    stages = [
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
    ]
    tags = db.query(Tag).order_by(Tag.name).all()
    categories = sorted({r.category for r in rows if r.category}, key=str.lower)
    return templates.TemplateResponse(
        request=request,
        name="prospects/list.html",
        context={
            "app_name": settings.app_name,
            "prospects": rows,
            "stages": stages,
            "tags": tags,
            "categories": categories,
            "current_stage": stage,
            "current_tag": tag,
            "current_category": category,
            "current_location": location,
            "current_q": q,
            "include_archived": include_archived,
            "do_not_contact_only": do_not_contact_only,
            "message": message,
            "message_type": message_type,
        },
    )


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe (no auth, no data)."""
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: DbSession) -> HTMLResponse:
    """Dashboard home page: pipeline strip + operational metrics + quick start."""
    counts = dashboard_service.stage_counts(db)
    metrics = dashboard_service.dashboard_metrics(db)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.app_name,
            "stage_counts": counts,
            "pipeline_phases": dashboard_service.PIPELINE_PHASES,
            "stage_label": dashboard_service.stage_label,
            "total_prospects": dashboard_service.total_prospects(db),
            "metrics": metrics,
            "needs_attention": dashboard_service.needs_attention(db),
        },
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


@router.get("/prospects/new", response_class=HTMLResponse)
def prospect_new_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="prospects/form.html",
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
        return templates.TemplateResponse(
            request=request,
            name="prospects/form.html",
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
        return templates.TemplateResponse(
            request=request,
            name="prospects/form.html",
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
    opportunities = opportunity_service.list_opportunities(db, prospect_id=prospect_id)
    return templates.TemplateResponse(
        request=request,
        name="prospects/detail.html",
        context={
            "app_name": settings.app_name,
            "prospect": prospect,
            "activities": activities,
            "latest_run": latest_run,
            "findings": findings,
            "website_checks": website_checks,
            "active_message": active_message,
            "follow_ups": follow_ups,
            "opportunities": opportunities,
        },
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
    return templates.TemplateResponse(
        request=request,
        name="prospects/form.html",
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
        return templates.TemplateResponse(
            request=request,
            name="prospects/form.html",
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
        return templates.TemplateResponse(
            request=request,
            name="prospects/form.html",
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
    reason: str = Form(...),
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
    return templates.TemplateResponse(
        request=request,
        name="settings/profile.html",
        context={
            "app_name": settings.app_name,
            "profile": profile,
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


@router.get("/settings/usage", response_class=HTMLResponse)
def usage_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Settings → Usage: provider + AI call accounting per day (§2.7)."""
    stats = dashboard_service.usage_stats(db)
    return templates.TemplateResponse(
        request=request,
        name="settings/usage.html",
        context={
            "app_name": settings.app_name,
            "providers": stats["providers"],
            "ai": stats["ai"],
        },
    )


@router.get("/settings/general", response_class=HTMLResponse)
def general_settings_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Settings → General: vocabulary, defaults, caps, backup (§2.7)."""
    return templates.TemplateResponse(
        request=request,
        name="settings/general.html",
        context={
            "app_name": settings.app_name,
            "settings": settings,
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
            return templates.TemplateResponse(
                request=request,
                name="settings/general.html",
                context={
                    "app_name": settings.app_name,
                    "settings": settings,
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
            return templates.TemplateResponse(
                request=request,
                name="settings/general.html",
                context={
                    "app_name": settings.app_name,
                    "settings": settings,
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
def import_form(request: Request) -> HTMLResponse:
    """CSV import wizard step 1: upload form."""
    return templates.TemplateResponse(
        request=request,
        name="settings/import.html",
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
            return templates.TemplateResponse(
                request=request,
                name="settings/import.html",
                context={
                    "app_name": settings.app_name,
                    "settings": settings,
                    "step": 1,
                    "error": str(exc),
                },
                status_code=400,
            )

    if preview is None:
        return templates.TemplateResponse(
            request=request,
            name="settings/import.html",
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

    return templates.TemplateResponse(
        request=request,
        name="settings/import.html",
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
        return templates.TemplateResponse(
            request=request,
            name="settings/import.html",
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

    return templates.TemplateResponse(
        request=request,
        name="settings/import.html",
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
    return templates.TemplateResponse(
        request=request,
        name="settings/saved_views.html",
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
) -> HTMLResponse | RedirectResponse:
    """Create a saved view from the HTML form."""

    filters_dict = None
    if filters and filters.strip():
        try:
            filters_dict = json.loads(filters)
        except json.JSONDecodeError:
            return HTMLResponse("Invalid JSON in filters field.", status_code=400)

    saved_views_service.create_saved_view(db, name=name, filters=filters_dict)
    return RedirectResponse(
        url=f"/settings/saved-views?flash={quote('View saved')}&flash_type=success",
        status_code=303,
    )


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
    return templates.TemplateResponse(
        request=request,
        name="merge/duplicates.html",
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
    return templates.TemplateResponse(
        request=request,
        name="settings/retention.html",
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

    return templates.TemplateResponse(
        request=request,
        name="prospects/research.html",
        context={
            "app_name": settings.app_name,
            "prospect": prospect,
            "runs": runs,
            "latest_run": latest_run,
            "findings": findings,
            "website_checks": website_checks,
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
    return templates.TemplateResponse(
        request=request,
        name="messages/edit.html",
        context={
            "app_name": settings.app_name,
            "prospect": prospect,
            "message": message,
            "findings": findings,
            "grounded_findings": grounded_findings,
        },
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
        return templates.TemplateResponse(
            request=request,
            name="messages/edit.html",
            context={
                "app_name": settings.app_name,
                "prospect": prospect,
                "message": message,
                "findings": findings,
                "grounded_findings": grounded_findings,
                "field_errors": exc.fields,
                "form": {"current_text": current_text},
            },
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
        return templates.TemplateResponse(
            request=request,
            name="messages/edit.html",
            context={
                "app_name": settings.app_name,
                "prospect": prospect,
                "message": message,
                "findings": findings,
                "grounded_findings": grounded_findings,
                "field_errors": exc.fields,
                "form": {"current_text": current_text},
            },
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
    return templates.TemplateResponse(
        request=request,
        name="messages/open.html",
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
    follow_up = db.get(FollowUp, follow_up_id)
    if follow_up is None:
        return HTMLResponse("Follow-up not found", status_code=404)
    follow_up.status = "done"
    follow_up.completed_at = datetime.now(UTC)
    db.flush()
    log_activity(
        db,
        prospect_id=follow_up.prospect_id,
        type="follow_up_completed",
        actor="user",
        payload={"follow_up_id": follow_up_id, "note": note},
    )
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
    follow_up = db.get(FollowUp, follow_up_id)
    if follow_up is None:
        return HTMLResponse("Follow-up not found", status_code=404)
    follow_up.status = "cancelled"
    db.flush()
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
    """Grouped list of messages needing attention: never lose an approved-but-unconfirmed message."""
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
    return templates.TemplateResponse(
        request=request,
        name="outreach/queue.html",
        context={"app_name": settings.app_name, "groups": groups},
    )
