"""Regression tests for website_status derivation (P6), the "no URL"
observation (C4/P5), and provider_usage daily upsert (H5).

Covers the review findings:
- C3: timeout/blocked/failed-search must never become `no_website_found`.
- C4: "No website URL recorded" is written only when no URL exists.
- Approved rule: no URL + successful empty search ⇒ `no_website_found`
  (DATABASE_DESIGN §2.7 amendment, USER_FLOWS Flow 2 step 6).
- H5: provider_usage upsert must not hit the unique index on the second
  call of a day (period column is DATE now).
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.main import app
from app.models import Finding, Opportunity, Prospect, ProviderUsage, ResearchRun, Source
from app.services import research as research_service
from app.services.prospect import create_prospect
from app.site_inspector import SiteInspection


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    return db, path


def _cleanup(db: Session, path: str) -> None:
    db.close()
    app.dependency_overrides.clear()
    with suppress(OSError):
        os.unlink(path)


def _add_prospect(db: Session, *, website_url: str | None = None) -> Prospect:
    source = db.query(Source).filter(Source.type == "manual").first()
    if source is None:
        source = Source(type="manual", label="Manual add", detail={})
        db.add(source)
        db.flush()
    prospect, _ = create_prospect(
        db,
        business_name="Acme Hotel",
        source=source,
        website_url=website_url,
        category="hotel",
        location_city="Berlin",
        actor="user",
        skip_dupe_check=True,
    )
    return prospect


def _store_check(db: Session, prospect_id: int, outcome: str) -> None:
    """Write a WebsiteCheck row as if an inspection had run."""
    research_service._store_website_check(
        db,
        prospect_id=prospect_id,
        research_run_id=None,
        inspection=SimpleNamespace(
            url="https://acme.example",
            outcome=outcome,
            http_status=None,
            final_url=None,
            response_ms=None,
            title=None,
            has_contact_path=False,
            has_booking_or_cta=False,
            signals={},
            error=f"boom: {outcome}" if outcome != "reachable" else None,
        ),
    )


def _derive(db: Session, prospect_id: int, *, has_url: bool, search_state: str) -> str:
    return research_service._derive_website_status(
        db,
        prospect_id,
        has_website_url=has_url,
        search_state=search_state,
    )


# ---------------------------------------------------------------------------
# _derive_website_status matrix (P6)
# ---------------------------------------------------------------------------


def test_reachable_wins_regardless_of_search() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        _store_check(db, p.id, "reachable")
        assert _derive(db, p.id, has_url=True, search_state="empty") == "reachable"
        assert _derive(db, p.id, has_url=True, search_state="failed") == "reachable"
    finally:
        _cleanup(db, path)


def test_dns_failure_with_empty_search_is_no_website() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        _store_check(db, p.id, "dns_failure")
        assert _derive(db, p.id, has_url=True, search_state="empty") == "no_website_found"
    finally:
        _cleanup(db, path)


def test_dns_failure_needs_successful_search_evidence() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        _store_check(db, p.id, "dns_failure")
        # A failed/absent search says nothing — could not verify (P6).
        assert _derive(db, p.id, has_url=True, search_state="failed") == "check_failed"
        assert _derive(db, p.id, has_url=True, search_state="no_provider") == "check_failed"
        assert _derive(db, p.id, has_url=True, search_state="results") == "check_failed"
    finally:
        _cleanup(db, path)


def test_timeout_and_blocked_are_never_no_website() -> None:
    """C3 regression: timeout/blocked mean could-not-verify, never no-website."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        for outcome in (
            "timeout",
            "blocked",
            "unreachable_http",
            "redirect_loop",
            "invalid_url",
            "not_attempted",
        ):
            _store_check(db, p.id, outcome)
            assert _derive(db, p.id, has_url=True, search_state="empty") == "check_failed", outcome
    finally:
        _cleanup(db, path)


def test_no_url_with_successful_empty_search_is_no_website() -> None:
    """Approved rule: no URL + search found nothing ⇒ no_website_found."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url=None)
        assert _derive(db, p.id, has_url=False, search_state="empty") == "no_website_found"
    finally:
        _cleanup(db, path)


def test_no_url_without_search_evidence_is_check_failed() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url=None)
        assert _derive(db, p.id, has_url=False, search_state="failed") == "check_failed"
        assert _derive(db, p.id, has_url=False, search_state="no_provider") == "check_failed"
        assert _derive(db, p.id, has_url=False, search_state="results") == "check_failed"
    finally:
        _cleanup(db, path)


def test_url_recorded_but_no_check_is_check_failed() -> None:
    """A URL exists but no inspection could be stored ⇒ could not verify."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        assert _derive(db, p.id, has_url=True, search_state="empty") == "check_failed"
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# run_research integration with fakes (no network — D12)
# ---------------------------------------------------------------------------


class _EmptySearch:
    provider_name = "empty_search"

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        return []


class _FailingSearch:
    provider_name = "failing_search"

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        raise RuntimeError("search down")


class _ResultSearch:
    provider_name = "result_search"

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        return [{"title": "Acme", "url": "https://acme.example", "snippet": "hotel"}]


def _fake_inspect(outcome: str, *, error: str | None = None):
    async def fake(url: str) -> SiteInspection:
        return SiteInspection(
            url=url,
            outcome=outcome,
            http_status=200 if outcome == "reachable" else None,
            final_url=None,
            response_ms=10,
            title="Acme Hotel" if outcome == "reachable" else None,
            has_contact_path=outcome == "reachable",
            has_booking_or_cta=False,
            signals={"cms": ["wordpress"]} if outcome == "reachable" else {},
            error=error,
        )

    return fake


def _run(db: Session, prospect: Prospect, *, search, inspect_fake=None) -> ResearchRun:
    """Run the research pipeline with fake providers patched in."""
    orig_search = research_service.get_search_provider
    orig_disc = research_service.get_discovery_provider
    orig_inspect = research_service.inspect
    research_service.get_search_provider = lambda: search
    research_service.get_discovery_provider = lambda: None
    if inspect_fake is not None:
        research_service.inspect = inspect_fake
    try:
        run = ResearchRun(prospect_id=prospect.id, triggered_by="user")
        db.add(run)
        db.flush()
        return research_service.run_research(db, prospect_id=prospect.id, research_run=run)
    finally:
        research_service.get_search_provider = orig_search
        research_service.get_discovery_provider = orig_disc
        research_service.inspect = orig_inspect


def test_c3_timeout_with_empty_search_is_check_failed() -> None:
    """C3: a timeout inspection must not be reported as 'no website'."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        _run(db, p, search=_EmptySearch(), inspect_fake=_fake_inspect("timeout", error="timed out"))
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated.website_status == "check_failed"
    finally:
        _cleanup(db, path)


def test_c3_failed_search_with_url_is_check_failed() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        _run(db, p, search=_FailingSearch(), inspect_fake=_fake_inspect("dns_failure"))
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated.website_status == "check_failed"
    finally:
        _cleanup(db, path)


def test_dns_failure_with_empty_search_is_no_website_found() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        _run(db, p, search=_EmptySearch(), inspect_fake=_fake_inspect("dns_failure"))
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated.website_status == "no_website_found"
    finally:
        _cleanup(db, path)


def test_c4_reachable_prospect_gets_no_no_url_finding() -> None:
    """C4 regression: prospects WITH a URL never get the 'no URL' observation."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        _run(db, p, search=_ResultSearch(), inspect_fake=_fake_inspect("reachable"))
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated.website_status == "reachable"
        findings = db.query(Finding).filter_by(prospect_id=p.id).all()
        assert not any(f.statement == research_service.NO_URL_STATEMENT for f in findings)
        assert any(f.statement.startswith("Website reachable:") for f in findings)
    finally:
        _cleanup(db, path)


def test_no_url_prospect_records_observation_and_opportunity() -> None:
    """No URL + successful empty search ⇒ no_website_found, the record-level
    observation exists (human/verified/high), and the flagship
    no_standalone_website opportunity is proposed with it as evidence."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url=None)
        _run(db, p, search=_EmptySearch())
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated.website_status == "no_website_found"

        finding = (
            db.query(Finding)
            .filter_by(prospect_id=p.id, statement=research_service.NO_URL_STATEMENT)
            .one()
        )
        assert finding.kind == "website"
        assert finding.origin == "human"
        assert finding.verification_status == "verified"

        opps = db.query(Opportunity).filter_by(prospect_id=p.id).all()
        assert [o.type for o in opps] == ["no_standalone_website"]
    finally:
        _cleanup(db, path)


def test_no_url_with_failed_search_gets_no_bogus_opportunity() -> None:
    """No URL + failed search ⇒ check_failed, and NO website_inaccessible
    proposal (the 'no URL' observation is not evidence of an inaccessible
    site — the misfire the C3 fix would otherwise introduce)."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url=None)
        _run(db, p, search=_FailingSearch())
        db.expire_all()
        updated = db.get(Prospect, p.id)
        assert updated.website_status == "check_failed"
        opps = db.query(Opportunity).filter_by(prospect_id=p.id).all()
        assert opps == []
    finally:
        _cleanup(db, path)


def test_reachable_inspection_records_single_usage_row() -> None:
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        _run(db, p, search=_EmptySearch(), inspect_fake=_fake_inspect("reachable"))
        rows = db.query(ProviderUsage).filter_by(provider="http_fetch", kind="inspect").all()
        assert len(rows) == 1
        assert rows[0].calls == 1
        assert rows[0].errors == 0
    finally:
        _cleanup(db, path)


def test_raising_inspect_records_single_usage_row_with_error() -> None:
    """Double-count regression: the exception path must record usage once,
    not twice (old code recorded in the handler AND after the block)."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")

        async def raising_inspect(url: str):
            raise RuntimeError("inspector exploded")

        _run(db, p, search=_EmptySearch(), inspect_fake=raising_inspect)
        rows = db.query(ProviderUsage).filter_by(provider="http_fetch", kind="inspect").all()
        assert len(rows) == 1
        assert rows[0].calls == 1
        assert rows[0].errors == 1
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# H5: provider_usage daily upsert (period is DATE)
# ---------------------------------------------------------------------------


def test_provider_usage_upsert_same_day_accumulates() -> None:
    """H5 regression: second upsert of the day must update, not violate the
    unique (provider, kind, period) index — including across sessions."""
    db, path = _make_db()
    try:
        research_service._record_provider_usage(db, provider="http_fetch", kind="inspect", calls=1)
        db.commit()

        research_service._record_provider_usage(
            db, provider="http_fetch", kind="inspect", calls=1, errors=1
        )
        db.commit()

        # Fresh session (new connection) — catches format mismatches.
        SessionLocal = sessionmaker(bind=db.get_bind())
        fresh_db = SessionLocal()
        try:
            rows = (
                fresh_db.query(ProviderUsage).filter_by(provider="http_fetch", kind="inspect").all()
            )
            assert len(rows) == 1
            assert rows[0].calls == 2
            assert rows[0].errors == 1
            assert rows[0].period is not None
        finally:
            fresh_db.close()
    finally:
        _cleanup(db, path)


def test_two_research_runs_same_day_do_not_crash() -> None:
    """QA defect H5 end-to-end: two run_research calls on the same day must
    both complete (the second used to die with IntegrityError)."""
    db, path = _make_db()
    try:
        p = _add_prospect(db, website_url="https://acme.example")
        _run(db, p, search=_EmptySearch(), inspect_fake=_fake_inspect("reachable"))
        _run(db, p, search=_EmptySearch(), inspect_fake=_fake_inspect("reachable"))
        rows = db.query(ProviderUsage).filter_by(provider="http_fetch", kind="inspect").all()
        assert len(rows) == 1
        assert rows[0].calls == 2
    finally:
        _cleanup(db, path)
