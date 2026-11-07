"""JSON API router (`/api/v1`).

Phase 1 endpoints: prospect CRUD, dedupe, stage transitions, activities,
and the user profile. Routes stay thin; logic lives in `app/services`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import (
    Activity,
    Finding,
    FollowUp,
    Prospect,
    ResearchRun,
    Source,
    WebsiteCheck,
)
from app.schemas import (
    ActivityOut,
    DedupeCheckOut,
    ErrorOut,
    FindingOut,
    FollowUpCreate,
    FollowUpOut,
    MessageConfirmOut,
    MessageOpenOut,
    OpportunityCreate,
    OpportunityOut,
    OutreachMessageOut,
    ProfileIn,
    ProfileOut,
    ProspectCreate,
    ProspectOut,
    ProspectPatch,
    ResearchRunOut,
    ResearchStartOut,
    StageTransitionIn,
    StageTransitionOut,
    WebsiteCheckOut,
)
from app.services import opportunity as opportunity_service
from app.services import outreach as outreach_service
from app.services import profile as profile_service
from app.services import prospect as prospect_service
from app.services import research as research_service
from app.whatsapp import normalize_phone

router = APIRouter(tags=["api"])

DbSession = Annotated[Session, Depends(get_db)]


def _prospect_to_out(prospect: Prospect) -> ProspectOut:
    return ProspectOut.model_validate(prospect)


@router.get("/prospects", response_model=list[ProspectOut])
def list_prospects(
    db: Session = Depends(get_db),
    stage: str | None = Query(None),
    tag: str | None = Query(None),
    category: str | None = Query(None),
    location: str | None = Query(None),
    q: str | None = Query(None),
    include_archived: bool = Query(False),
    do_not_contact_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[ProspectOut]:
    rows = prospect_service.list_prospects(
        db,
        stage=stage,
        tag=tag,
        category=category,
        location=location,
        q=q,
        include_archived=include_archived,
        do_not_contact_only=do_not_contact_only,
        limit=limit,
        offset=offset,
    )
    return [_prospect_to_out(r) for r in rows]


@router.post(
    "/prospects",
    response_model=ProspectOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorOut}},
)
def create_prospect(
    payload: ProspectCreate,
    db: Session = Depends(get_db),
) -> ProspectOut:
    # Resolve or create a default "manual" source.
    source = db.query(Source).filter(Source.type == "manual").first()
    if source is None:
        source = Source(type="manual", label="Manual add", detail={})
        db.add(source)
        db.flush()

    # Normalize the raw phone to E.164 for dedupe + WhatsApp (D9). An explicit
    # phone_e164 from the client is trusted; otherwise normalize phone_raw.
    phone_e164 = payload.phone_e164
    if phone_e164 is None and payload.phone_raw:
        phone_e164 = normalize_phone(payload.phone_raw, settings.phone_default_country)

    prospect, error = prospect_service.create_prospect(
        db,
        business_name=payload.business_name,
        source=source,
        category=payload.category,
        location_area=payload.location_area,
        location_city=payload.location_city,
        location_state=payload.location_state,
        location_country=payload.location_country,
        address=payload.address,
        phone_e164=phone_e164,
        phone_raw=payload.phone_raw,
        email=str(payload.email) if payload.email else None,
        website_url=payload.website_url,
        social_links=payload.social_links,
    )
    if error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert prospect is not None
    return _prospect_to_out(prospect)


@router.get("/prospects/{prospect_id}", response_model=ProspectOut)
def get_prospect(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> ProspectOut:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect not found")
    assert prospect is not None
    return _prospect_to_out(prospect)


@router.patch(
    "/prospects/{prospect_id}",
    response_model=ProspectOut,
    responses={409: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)
def update_prospect(
    prospect_id: int = Path(ge=1),
    payload: ProspectPatch = Body(...),
    db: Session = Depends(get_db),
) -> ProspectOut:
    update_data = payload.model_dump(exclude_unset=True)
    prospect, error = prospect_service.update_prospect(db, prospect_id, **update_data)
    if error:
        if error == "Prospect not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert prospect is not None
    return _prospect_to_out(prospect)


@router.post(
    "/prospects/{prospect_id}/archive",
    response_model=ProspectOut,
    responses={404: {"model": ErrorOut}},
)
def archive_prospect(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> ProspectOut:
    ok = prospect_service.archive_prospect(db, prospect_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect not found")
    prospect = db.get(Prospect, prospect_id)
    assert prospect is not None
    return _prospect_to_out(prospect)


@router.post(
    "/prospects/{prospect_id}/unarchive",
    response_model=ProspectOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def unarchive_prospect(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> ProspectOut:
    ok = prospect_service.unarchive_prospect(db, prospect_id)
    if not ok:
        # Could be not-found or blocked by dedupe; treat as conflict for now.
        prospect = db.get(Prospect, prospect_id)
        if prospect is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect not found")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot unarchive: a live prospect with the same identity already exists",
        )
    prospect = db.get(Prospect, prospect_id)
    assert prospect is not None
    return _prospect_to_out(prospect)


@router.get(
    "/prospects/{prospect_id}/dedupe",
    response_model=DedupeCheckOut,
    responses={404: {"model": ErrorOut}},
)
def check_prospect_dupes(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> DedupeCheckOut:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect not found")
    dupe = prospect_service.check_duplicates(
        db,
        business_name=prospect.business_name,
        phone_e164=prospect.phone_e164,
        email=prospect.email,
        website_url=prospect.website_url,
        location_city=prospect.location_city,
        exclude_id=prospect.id,
    )
    return DedupeCheckOut(
        exact_match=dupe["exact"] is not None,
        exact_prospect_id=dupe["exact"].id if dupe["exact"] else None,
        exact_prospect_name=dupe["exact"].business_name if dupe["exact"] else None,
        fuzzy_flags=[p.id for p in dupe["fuzzy"]],
        normalized_name=dupe["normalized_name"] or "",
    )


@router.post(
    "/prospects/{prospect_id}/stage",
    response_model=StageTransitionOut,
    responses={409: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)
def transition_prospect_stage(
    prospect_id: int = Path(ge=1),
    payload: StageTransitionIn = Body(...),
    db: Session = Depends(get_db),
) -> StageTransitionOut:
    prospect, error = prospect_service.transition_stage(db, prospect_id, payload.stage)
    if error:
        if error == "Prospect not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    return StageTransitionOut.model_validate(prospect)


@router.get(
    "/prospects/{prospect_id}/activities",
    response_model=list[ActivityOut],
    responses={404: {"model": ErrorOut}},
)
def list_activities(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> list[ActivityOut]:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect not found")
    rows = (
        db.query(Activity)
        .filter_by(prospect_id=prospect_id)
        .order_by(Activity.created_at.desc())
        .all()
    )
    return [ActivityOut.model_validate(r) for r in rows]


@router.get("/settings/profile", response_model=ProfileOut)
def get_profile(db: DbSession) -> ProfileOut:
    profile = profile_service.get_or_create_profile(db)
    return ProfileOut.model_validate(profile)


@router.patch("/settings/profile", response_model=ProfileOut)
def update_profile(
    payload: ProfileIn,
    db: Session = Depends(get_db),
) -> ProfileOut:
    profile = profile_service.update_profile(
        db,
        offer=payload.offer,
        target_industries=payload.target_industries,
        target_locations=payload.target_locations,
        ideal_signals=payload.ideal_signals,
    )
    return ProfileOut.model_validate(profile)


@router.post(
    "/prospects/{prospect_id}/research/runs",
    response_model=ResearchStartOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"model": ErrorOut}},
)
def start_research(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> ResearchStartOut:
    """Queue a research run for a prospect."""
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect not found")

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

    return ResearchStartOut(
        run_id=run.id,
        job_id=None,
        status=run.status,
    )


@router.get(
    "/research/runs/{run_id}",
    response_model=ResearchRunOut,
    responses={404: {"model": ErrorOut}},
)
def get_research_run(
    run_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> ResearchRunOut:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found")
    return ResearchRunOut.model_validate(run)


@router.get(
    "/prospects/{prospect_id}/findings",
    response_model=list[FindingOut],
    responses={404: {"model": ErrorOut}},
)
def list_findings(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> list[FindingOut]:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect not found")
    rows = (
        db.query(Finding)
        .filter_by(prospect_id=prospect_id)
        .order_by(Finding.recorded_at.desc())
        .all()
    )
    return [FindingOut.model_validate(r) for r in rows]


@router.get(
    "/prospects/{prospect_id}/website-checks",
    response_model=list[WebsiteCheckOut],
    responses={404: {"model": ErrorOut}},
)
def list_website_checks(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> list[WebsiteCheckOut]:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect not found")
    rows = (
        db.query(WebsiteCheck)
        .filter_by(prospect_id=prospect_id)
        .order_by(WebsiteCheck.checked_at.desc())
        .all()
    )
    return [WebsiteCheckOut.model_validate(r) for r in rows]


# ===== Opportunities (Flow 4) =====


@router.get(
    "/prospects/{prospect_id}/opportunities",
    response_model=list[OpportunityOut],
    responses={404: {"model": ErrorOut}},
)
def list_opportunities_api(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> list[OpportunityOut]:
    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect not found")
    rows = opportunity_service.list_opportunities(db, prospect_id=prospect_id)
    return [OpportunityOut.model_validate(r) for r in rows]


@router.post(
    "/prospects/{prospect_id}/opportunities",
    response_model=OpportunityOut,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def create_opportunity_api(
    prospect_id: int = Path(ge=1),
    payload: OpportunityCreate = Body(...),
    db: Session = Depends(get_db),
) -> OpportunityOut:
    opportunity, error = opportunity_service.create_opportunity(
        db,
        prospect_id=prospect_id,
        type=payload.type,
        summary=payload.summary,
        rationale=payload.rationale,
        origin="human",
        actor="user",
    )
    if error:
        if error == "Prospect not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert opportunity is not None
    return OpportunityOut.model_validate(opportunity)


@router.post(
    "/opportunities/{opportunity_id}/validate",
    response_model=OpportunityOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def validate_opportunity_api(
    opportunity_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> OpportunityOut:
    opportunity, error = opportunity_service.validate_opportunity(
        db, opportunity_id=opportunity_id, actor="user"
    )
    if error:
        if error == "Opportunity not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert opportunity is not None
    return OpportunityOut.model_validate(opportunity)


@router.post(
    "/opportunities/{opportunity_id}/dismiss",
    response_model=OpportunityOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def dismiss_opportunity_api(
    opportunity_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> OpportunityOut:
    opportunity, error = opportunity_service.dismiss_opportunity(
        db, opportunity_id=opportunity_id, actor="user"
    )
    if error:
        if error == "Opportunity not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert opportunity is not None
    return OpportunityOut.model_validate(opportunity)


# ===== Outreach messages (Flow 5) =====


@router.post(
    "/prospects/{prospect_id}/drafts",
    response_model=OutreachMessageOut,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def generate_draft_api(
    prospect_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> OutreachMessageOut:
    """Generate a draft from research findings (Flow 5)."""
    message, error = outreach_service.generate_draft(db, prospect_id=prospect_id, actor="user")
    if error:
        if error == "Prospect not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        if error.startswith("suppressed:"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert message is not None
    return OutreachMessageOut.model_validate(message)


@router.patch(
    "/messages/{message_id}",
    response_model=OutreachMessageOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def edit_message_api(
    message_id: int = Path(ge=1),
    current_text: str = Body(..., embed=True),
    db: Session = Depends(get_db),
) -> OutreachMessageOut:
    """Edit message text — any edit demotes the message back to draft (§3.1)."""
    message, error = outreach_service.edit_message(
        db, message_id=message_id, current_text=current_text, actor="user"
    )
    if error:
        if error == "Message not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert message is not None
    return OutreachMessageOut.model_validate(message)


@router.post(
    "/messages/{message_id}/submit",
    response_model=OutreachMessageOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def submit_message_api(
    message_id: int = Path(ge=1),
    current_text: str = Body(..., embed=True),
    db: Session = Depends(get_db),
) -> OutreachMessageOut:
    """Submit a draft for review: draft → pending_review."""
    message, error = outreach_service.submit_message(
        db, message_id=message_id, current_text=current_text, actor="user"
    )
    if error:
        if error == "Message not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert message is not None
    return OutreachMessageOut.model_validate(message)


@router.post(
    "/messages/{message_id}/approve",
    response_model=OutreachMessageOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def approve_message_api(
    message_id: int = Path(ge=1),
    ungrounded_acknowledgement: bool = Body(False, embed=True),
    db: Session = Depends(get_db),
) -> OutreachMessageOut:
    """Approve for sending: pending_review → approved (guards applied)."""
    message, error = outreach_service.approve_message(
        db,
        message_id=message_id,
        ungrounded_acknowledgement=ungrounded_acknowledgement,
        actor="user",
    )
    if error:
        if error == "Message not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        if error.startswith("suppressed:"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert message is not None
    return OutreachMessageOut.model_validate(message)


@router.post(
    "/messages/{message_id}/open",
    response_model=MessageOpenOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def open_message_api(
    message_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> MessageOpenOut:
    """Open in WhatsApp: approved → opened_in_whatsapp, returns wa.me URL."""
    message, error, wa_me_url = outreach_service.open_message(
        db, message_id=message_id, actor="user"
    )
    if error:
        if error == "Message not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        if error.startswith("suppressed:"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert message is not None
    return MessageOpenOut(status=message.status, wa_me_url=wa_me_url)


@router.post(
    "/messages/{message_id}/confirm-sent",
    response_model=MessageConfirmOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def confirm_sent_api(
    message_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> MessageConfirmOut:
    """Confirm the user sent the message: opened_in_whatsapp → sent_confirmed."""
    message, error = outreach_service.confirm_sent(db, message_id=message_id, actor="user")
    if error:
        if error == "Message not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert message is not None
    return MessageConfirmOut(status=message.status, last_contacted_at=message.sent_confirmed_at)


# ===== Follow-ups (Flow 7) =====


@router.post(
    "/prospects/{prospect_id}/follow-ups",
    response_model=FollowUpOut,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorOut}},
)
def create_follow_up_api(
    prospect_id: int = Path(ge=1),
    payload: FollowUpCreate = Body(...),
    db: Session = Depends(get_db),
) -> FollowUpOut:
    follow_up, error = outreach_service.create_follow_up(
        db, prospect_id=prospect_id, due_date=payload.due_date, note=payload.note, actor="user"
    )
    if error:
        if error == "Prospect not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error)
    assert follow_up is not None
    return FollowUpOut.model_validate(follow_up)


@router.post(
    "/follow-ups/{follow_up_id}/complete",
    response_model=FollowUpOut,
    responses={404: {"model": ErrorOut}},
)
def complete_follow_up_api(
    follow_up_id: int = Path(ge=1),
    note: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
) -> FollowUpOut:
    follow_up = db.get(FollowUp, follow_up_id)
    if follow_up is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Follow-up not found")
    follow_up.status = "done"
    follow_up.completed_at = datetime.now(UTC)
    db.flush()
    return FollowUpOut.model_validate(follow_up)


@router.post(
    "/follow-ups/{follow_up_id}/cancel",
    response_model=FollowUpOut,
    responses={404: {"model": ErrorOut}},
)
def cancel_follow_up_api(
    follow_up_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> FollowUpOut:
    follow_up = db.get(FollowUp, follow_up_id)
    if follow_up is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Follow-up not found")
    follow_up.status = "cancelled"
    db.flush()
    return FollowUpOut.model_validate(follow_up)
