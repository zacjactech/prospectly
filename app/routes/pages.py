"""Page routes (HTML). Thin: no domain logic here (AGENTS.md §5)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR, settings
from app.db import get_db
from app.models import Activity, Prospect, Source, Tag
from app.services import profile as profile_service
from app.services import prospect as prospect_service

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
def index(request: Request) -> HTMLResponse:
    """Dashboard home page."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": settings.app_name},
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
        phone_e164=None,  # normalization happens via whatsapp.py in Phase 2
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
    return RedirectResponse(url=f"/prospects/{prospect.id}", status_code=303)


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
    return templates.TemplateResponse(
        request=request,
        name="prospects/detail.html",
        context={
            "app_name": settings.app_name,
            "prospect": prospect,
            "activities": activities,
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
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


@router.post("/prospects/{prospect_id}/archive", response_class=HTMLResponse, response_model=None)
def prospect_archive(
    prospect_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    ok = prospect_service.archive_prospect(db, prospect_id)
    if not ok:
        return HTMLResponse("Prospect not found", status_code=404)
    return RedirectResponse(url="/prospects", status_code=303)


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
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


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
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


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
    return RedirectResponse(url="/settings/profile", status_code=303)
