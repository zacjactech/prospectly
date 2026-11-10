"""Research service — runs the per-prospect evidence pipeline.

Pipeline steps (SYSTEM_ARCHITECTURE §4.1):
1. Website inspection (SafeFetcher + SiteInspector)
2. Web search (enabled SearchProvider)
3. Optional discovery enrichment (OSM Overpass)
4. Optional AI summarization (if configured)

Each result is stored as a `findings` row with source metadata (P7).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Finding, Prospect, ProviderUsage, ResearchRun
from app.providers.factory import get_discovery_provider, get_search_provider
from app.services.activity import log_activity
from app.site_inspector import inspect

# Record-level observation written only when a prospect has NO website URL.
# It feeds the `no_standalone_website` evidence (opportunity rule engine) and
# must never be written for prospects that DO have a URL (P5).
NO_URL_STATEMENT = "No website URL recorded for this prospect"


def _record_provider_usage(
    db: Session,
    *,
    provider: str,
    kind: str,
    calls: int = 1,
    errors: int = 0,
    rate_limited: int = 0,
) -> None:
    """Record provider call accounting (SYSTEM_ARCHITECTURE §6).

    Upserts into ``provider_usage`` keyed by (provider, kind, period).
    """
    from datetime import date as _date

    today = _date.today()
    row = db.query(ProviderUsage).filter_by(provider=provider, kind=kind, period=today).first()
    if row is None:
        row = ProviderUsage(
            provider=provider,
            kind=kind,
            calls=calls,
            errors=errors,
            rate_limited=rate_limited,
            period=today,
        )
        db.add(row)
    else:
        row.calls += calls
        row.errors += errors
        row.rate_limited += rate_limited
    db.flush()


def _now() -> datetime:
    return datetime.now(UTC)


def _finding_key(kind: str, statement: str) -> str:
    """Stable hash of (kind, normalized statement) for idempotent re-import."""
    normalized = " ".join(statement.lower().split())
    return hashlib.sha256(f"{kind}:{normalized}".encode()).hexdigest()[:32]


def _upsert_finding(
    db: Session,
    *,
    prospect_id: int,
    research_run_id: int | None,
    kind: str,
    statement: str,
    detail: str | None = None,
    source_url: str | None = None,
    source_type: str,
    verification_status: str = "unverified",
    confidence: str = "low",
    origin: str = "provider",
) -> Finding:
    """Create or update a finding idempotently by (prospect_id, finding_key)."""
    key = _finding_key(kind, statement)
    existing = db.query(Finding).filter_by(prospect_id=prospect_id, finding_key=key).first()
    if existing:
        # Update non-user-edited fields only if the existing row is provider-origin
        # and unverified (human edits take precedence).
        if existing.origin == "provider" and existing.verification_status == "unverified":
            existing.statement = statement
            existing.detail = detail
            existing.source_url = source_url
            existing.source_type = source_type
            existing.verification_status = verification_status
            existing.confidence = confidence
            existing.origin = origin
            existing.retrieved_at = _now()
        return existing

    finding = Finding(
        prospect_id=prospect_id,
        research_run_id=research_run_id,
        kind=kind,
        statement=statement,
        detail=detail,
        source_url=source_url,
        source_type=source_type,
        retrieved_at=_now(),
        recorded_at=_now(),
        verification_status=verification_status,
        confidence=confidence,
        origin=origin,
        finding_key=key,
    )
    db.add(finding)
    db.flush()
    return finding


def _store_website_check(
    db: Session,
    *,
    prospect_id: int,
    research_run_id: int | None,
    inspection: Any,
) -> None:
    from app.models import WebsiteCheck

    check = WebsiteCheck(
        prospect_id=prospect_id,
        run_id=research_run_id,
        url_checked=inspection.url,
        outcome=inspection.outcome,
        http_status=inspection.http_status,
        final_url=inspection.final_url,
        response_ms=inspection.response_ms,
        title=inspection.title,
        has_contact_path=inspection.has_contact_path,
        has_booking_or_cta=inspection.has_booking_or_cta,
        signals=inspection.signals,
        error=inspection.error,
        checked_at=_now(),
    )
    db.add(check)
    db.flush()


def _derive_website_status(
    db: Session,
    prospect_id: int,
    *,
    has_website_url: bool,
    search_state: str,
) -> str:
    """Derive prospects.website_status from latest check + search evidence (P6).

    Rule (DATABASE_DESIGN §2.7, USER_FLOWS Flow 2 step 6):
    - inspection reachable ⇒ ``reachable``
    - DNS failure — or no URL recorded at all — AND a *successful* search
      that found nothing ⇒ ``no_website_found`` (search-empty is the evidence
      when there is nothing to DNS-check; approved amendment, see
      DATABASE_DESIGN §2.7)
    - everything else ⇒ ``check_failed``: timeout/blocked/HTTP errors and
      failed or absent searches mean "could not verify" — never "no website"
      (P6).
    """
    from app.models import WebsiteCheck

    latest = (
        db.query(WebsiteCheck)
        .filter_by(prospect_id=prospect_id)
        .order_by(WebsiteCheck.checked_at.desc())
        .first()
    )
    if latest is not None:
        if latest.outcome == "reachable":
            return "reachable"
        if latest.outcome == "dns_failure" and search_state == "empty":
            return "no_website_found"
        return "check_failed"

    # No check recorded: only a no-URL prospect can be confirmed via search.
    if not has_website_url and search_state == "empty":
        return "no_website_found"
    return "check_failed"


def run_research(
    db: Session,
    *,
    prospect_id: int,
    research_run: ResearchRun,
) -> ResearchRun:
    """Execute the full research pipeline for one prospect.

    Updates the research_run status and prospect stage. All findings are
    stored with source metadata. Never raises — failures are captured as
    partially_completed with error info.
    """

    prospect = db.get(Prospect, prospect_id)
    if prospect is None:
        research_run.status = "failed"
        research_run.error = "prospect not found"
        db.flush()
        return research_run

    research_run.status = "running"
    research_run.started_at = _now()
    db.flush()

    steps: dict[str, str] = {}
    # P6 evidence quality: only a SUCCESSFUL search that found nothing may
    # support "no website". States: no_provider | results | empty | failed.
    search_state = "no_provider"
    errors: list[str] = []

    # --- Step 1: Website inspection ---
    website_url = prospect.website_url
    if website_url:
        inspect_errored = False
        try:
            inspection = _run_sync(inspect(website_url))
            _store_website_check(
                db,
                prospect_id=prospect_id,
                research_run_id=research_run.id,
                inspection=inspection,
            )
            if inspection.is_reachable:
                steps["website_check"] = "ok"
                _upsert_finding(
                    db,
                    prospect_id=prospect_id,
                    research_run_id=research_run.id,
                    kind="website",
                    statement=f"Website reachable: {inspection.url}",
                    detail=f"HTTP {inspection.http_status}, title: {inspection.title or 'none'}",
                    source_url=inspection.url,
                    source_type="website_fetch",
                    verification_status="unverified",
                    confidence="medium",
                    origin="provider",
                )
                if inspection.has_contact_path:
                    _upsert_finding(
                        db,
                        prospect_id=prospect_id,
                        research_run_id=research_run.id,
                        kind="contact",
                        statement="Website has a contact path (form/mailto/tel/WhatsApp link)",
                        source_url=inspection.url,
                        source_type="website_fetch",
                        verification_status="unverified",
                        confidence="medium",
                        origin="provider",
                    )
                signals = inspection.signals or {}
                if signals.get("cms"):
                    _upsert_finding(
                        db,
                        prospect_id=prospect_id,
                        research_run_id=research_run.id,
                        kind="website_quality",
                        statement=f"Site uses CMS/tech: {', '.join(signals['cms'])}",
                        source_url=inspection.url,
                        source_type="website_fetch",
                        verification_status="unverified",
                        confidence="low",
                        origin="provider",
                    )
            else:
                steps["website_check"] = f"failed: {inspection.outcome}"
                if inspection.error:
                    errors.append(f"website: {inspection.error}")
        except Exception as exc:  # pragma: no cover - defensive
            inspect_errored = True
            steps["website_check"] = f"failed: {exc!s}"
            errors.append(f"website: {exc!s}")
        # Exactly one usage record per attempted inspection (the old code
        # recorded on both the exception path and afterwards: double count).
        _record_provider_usage(
            db,
            provider="http_fetch",
            kind="inspect",
            calls=1,
            errors=1 if inspect_errored else 0,
        )
    else:
        steps["website_check"] = "not_attempted"
        # C4 fix: record the observation only when NO URL exists. Wording is
        # record-level ("recorded"), not a business claim; website_status
        # carries the P6 verdict derived from search/DNS evidence.
        _upsert_finding(
            db,
            prospect_id=prospect_id,
            research_run_id=research_run.id,
            kind="website",
            statement=NO_URL_STATEMENT,
            source_type="user",
            verification_status="verified",
            confidence="high",
            origin="human",
        )

    # --- Step 2: Web search ---
    search_provider = get_search_provider()
    if search_provider:
        try:
            search_query = f"{prospect.business_name} {prospect.location_city or ''}".strip()
            results = _run_sync(search_provider.search(search_query, max_results=5))
            if results:
                search_state = "results"
                steps["search"] = f"ok ({len(results)} results)"
                for r in results:
                    _upsert_finding(
                        db,
                        prospect_id=prospect_id,
                        research_run_id=research_run.id,
                        kind="identity",
                        statement=f"{r.get('title', '')} — {r.get('snippet', '')}",
                        source_url=r.get("url"),
                        source_type="search_result",
                        verification_status="unverified",
                        confidence="low",
                        origin="provider",
                    )
            else:
                search_state = "empty"
                steps["search"] = "ok (no results)"
        except Exception as exc:  # pragma: no cover - defensive
            search_state = "failed"
            steps["search"] = f"failed: {exc!s}"
            errors.append(f"search: {exc!s}")
            _record_provider_usage(
                db,
                provider=getattr(search_provider, "provider_name", "search"),
                kind="search",
                calls=1,
                errors=1,
            )
        else:
            _record_provider_usage(
                db,
                provider=getattr(search_provider, "provider_name", "search"),
                kind="search",
                calls=1,
            )
    else:
        steps["search"] = "skipped (no provider)"

    # --- Step 3: Discovery enrichment ---
    discovery_provider = get_discovery_provider()
    if discovery_provider and search_state != "results":
        discovery_provider_name = getattr(discovery_provider, "provider_name", "discovery")
        try:
            candidates = _run_sync(
                discovery_provider.discover(
                    industries=[prospect.category] if prospect.category else [],
                    locations=[prospect.location_city or "", prospect.location_area or ""],
                    max_results=5,
                )
            )
            if candidates:
                steps["discovery"] = f"ok ({len(candidates)} candidates)"
                for c in candidates:
                    _upsert_finding(
                        db,
                        prospect_id=prospect_id,
                        research_run_id=research_run.id,
                        kind="identity",
                        statement=f"Possible related business: {c.get('name', '')} ({c.get('category', '')})",
                        source_url=c.get("website"),
                        source_type=discovery_provider_name,
                        verification_status="unverified",
                        confidence="low",
                        origin="provider",
                    )
            else:
                steps["discovery"] = "ok (no candidates)"
        except Exception as exc:  # pragma: no cover - defensive
            steps["discovery"] = f"failed: {exc!s}"
            errors.append(f"discovery: {exc!s}")
            _record_provider_usage(
                db,
                provider=discovery_provider_name,
                kind="discover",
                calls=1,
                errors=1,
            )
        else:
            _record_provider_usage(
                db,
                provider=discovery_provider_name,
                kind="discover",
                calls=1,
            )
    else:
        steps["discovery"] = "skipped"

    # --- Derive website status ---
    website_status = _derive_website_status(
        db,
        prospect_id,
        has_website_url=website_url is not None,
        search_state=search_state,
    )
    prospect.website_status = website_status

    # --- Finalize run ---
    research_run.steps = steps
    research_run.finished_at = _now()
    if errors:
        research_run.status = "partially_completed"
        research_run.error = "; ".join(errors)
    else:
        research_run.status = "succeeded"

    # Advance prospect stage
    if prospect.stage == "new":
        prospect.stage = "researched"

    # Flow 4: after research, the rule engine proposes evidence-backed
    # opportunities for the operator to validate or dismiss.
    from app.services.opportunity import propose_opportunities

    propose_opportunities(db, prospect_id=prospect_id, research_run_id=research_run.id)

    db.flush()

    log_activity(
        db,
        prospect_id=prospect_id,
        type="research_finished",
        actor="system",
        payload={
            "run_id": research_run.id,
            "status": research_run.status,
            "website_status": website_status,
            "steps": steps,
        },
    )

    return research_run


def _run_sync(coro: Any) -> Any:
    """Run an async coroutine synchronously from a sync service context.

    FastAPI routes are sync; the research service is called from sync routes.
    We use asyncio.run() since we're not inside an event loop.
    """
    import asyncio

    return asyncio.run(coro)
