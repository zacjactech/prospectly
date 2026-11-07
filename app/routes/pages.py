"""Page routes (HTML). Thin: no domain logic here (AGENTS.md §5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR, settings
from app.db import get_db
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
from app.services import opportunity as opportunity_service
from app.services import outreach as outreach_service
from app.services import profile as profile_service
from app.services import prospect as prospect_service
from app.services import research as research_service
from app.services.activity import log_activity
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
    source = db.query(Source).filter(Source.type == "manual").first()
    if source is None:
        source = Source(type="manual", label="Manual add", detail={})
        db.add(source)
        db.flush()

    # Normalize the raw phone to E.164 for dedupe + WhatsApp (D9). Invalid
    # numbers stay in phone_raw only and never produce a wa.me recipient.
    phone_e164 = normalize_phone(phone_raw, settings.phone_default_country) if phone_raw else None

    prospect, error = prospect_service.create_prospect(
        db,
        business_name=business_name,
        source=source,
        category=category or None,
        location_area=location_area or None,
        location_city=location_city or None,
        location_state=location_state or None,
        location_country=location_country or None,
        address=address or None,
        phone_e164=phone_e164,
        phone_raw=phone_raw or None,
        email=email or None,
        website_url=website_url or None,
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
    prospect, error = prospect_service.update_prospect(
        db,
        prospect_id,
        business_name=business_name,
        category=category or None,
        location_area=location_area or None,
        location_city=location_city or None,
        location_state=location_state or None,
        location_country=location_country or None,
        address=address or None,
        phone_e164=normalize_phone(phone_raw, settings.phone_default_country)
        if phone_raw
        else None,
        phone_raw=phone_raw or None,
        email=email or None,
        website_url=website_url or None,
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
    def _csv_to_list(value: str | None) -> list[str] | None:
        if value is None or value.strip() == "":
            return []
        return [v.strip() for v in value.split(",") if v.strip()]

    profile_service.update_profile(
        db,
        offer=offer or None,
        target_industries=_csv_to_list(target_industries),
        target_locations=_csv_to_list(target_locations),
        ideal_signals=_csv_to_list(ideal_signals),
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

    try:
        research_service.run_research(db, prospect_id=prospect_id, research_run=run)
    except Exception as exc:  # pragma: no cover - defensive
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = research_service._now()
        db.flush()

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
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        return HTMLResponse("Prospect not found", status_code=404)
    opportunity, error = opportunity_service.create_opportunity(
        db,
        prospect_id=prospect_id,
        type=type,
        summary=summary,
        rationale=rationale,
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
    message, error = outreach_service.edit_message(
        db, message_id=message_id, current_text=current_text, actor="user"
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
    message, error = outreach_service.submit_message(
        db, message_id=message_id, current_text=current_text, actor="user"
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
    parsed_due = None
    if due_date:
        try:
            parsed_due = datetime.fromisoformat(due_date)
        except ValueError:
            parsed_due = None
    follow_up, error = outreach_service.create_follow_up(
        db, prospect_id=prospect_id, due_date=parsed_due, note=note, actor="user"
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
