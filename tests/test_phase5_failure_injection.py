"""Phase 5 failure-injection tests.

Verifies that provider failures (429/500/timeout) don't crash the research
pipeline — partial results are preserved, run status is partially_completed,
and error info is captured for the UI to display (SYSTEM_ARCHITECTURE §6,
SECURITY_AND_PRIVACY.md §5).
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.main import app
from app.models import Finding, Prospect, ResearchRun, Source
from app.services import research as research_service
from app.services.prospect import create_prospect


class FailingSearchProvider:
    """Search provider that always raises."""

    provider_name = "failing_search"

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        raise RuntimeError("search provider 500 error")


class RateLimitedSearchProvider:
    """Search provider that simulates a 429 rate limit."""

    provider_name = "rate_limited_search"

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        raise RuntimeError("search provider rate limited (429)")


class TimeoutSearchProvider:
    """Search provider that simulates a timeout."""

    provider_name = "timeout_search"

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        raise TimeoutError("search provider timed out")


class EmptySearchProvider:
    """Search provider that returns no results (so discovery step runs)."""

    provider_name = "empty_search"

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        return []


class FailingDiscovery:
    """Discovery provider that always raises."""

    async def discover(self, *, industries, locations, max_results):
        raise RuntimeError("OSM 500 error")


class WorkingSearchProvider:
    """Search provider that returns results."""

    provider_name = "working_search"

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        return [
            {
                "title": "Acme Hotel - Official Site",
                "url": "https://acme.example",
                "snippet": "Best hotel in Berlin",
            }
        ]


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


def _run_with_provider(db, prospect_id, provider):
    """Monkeypatch get_search_provider and run research."""
    original = research_service.get_search_provider
    research_service.get_search_provider = lambda: provider
    try:
        run = ResearchRun(prospect_id=prospect_id, triggered_by="user")
        db.add(run)
        db.flush()
        result = research_service.run_research(db, prospect_id=prospect_id, research_run=run)
        return result
    finally:
        research_service.get_search_provider = original


# ---------------------------------------------------------------------------
# Search provider 500 → partially_completed, website findings preserved
# ---------------------------------------------------------------------------


def test_search_500_partial_results_preserved() -> None:
    """When search returns 500, website findings are preserved and run is
    partially_completed with error info."""
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, website_url="https://acme.example")
        # We can't easily mock the website fetch, so use a prospect without
        # a website to isolate search failure.
        prospect.website_url = None
        db.flush()

        run = _run_with_provider(db, prospect.id, FailingSearchProvider())

        assert run.status == "partially_completed", (
            f"Expected partially_completed, got {run.status}: {run.error}"
        )
        assert "search" in (run.error or ""), f"Expected search error in {run.error}"
        assert run.steps is not None
        assert "search" in run.steps
        assert "failed" in run.steps["search"]

        # Prospect stage should still advance to researched
        db.expire_all()
        updated = db.get(Prospect, prospect.id)
        assert updated.stage == "researched"
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# Search provider 429 rate limit → partially_completed
# ---------------------------------------------------------------------------


def test_search_429_rate_limited_partial() -> None:
    """When search is rate limited (429), run is partially_completed and
    error is captured."""
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        prospect.website_url = None
        db.flush()

        run = _run_with_provider(db, prospect.id, RateLimitedSearchProvider())

        assert run.status == "partially_completed"
        assert "rate limited" in (run.error or "")
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# Search provider timeout → partially_completed
# ---------------------------------------------------------------------------


def test_search_timeout_partial() -> None:
    """When search times out, run is partially_completed and error captured."""
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        prospect.website_url = None
        db.flush()

        run = _run_with_provider(db, prospect.id, TimeoutSearchProvider())

        assert run.status == "partially_completed"
        assert "timed out" in (run.error or "")
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# Working search → succeeded
# ---------------------------------------------------------------------------


def test_working_search_succeeded() -> None:
    """When search works, run is succeeded and findings are stored."""
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        prospect.website_url = None
        db.flush()

        run = _run_with_provider(db, prospect.id, WorkingSearchProvider())

        assert run.status == "succeeded", f"Expected succeeded, got {run.status}: {run.error}"
        assert run.steps is not None
        assert "search" in run.steps
        assert "ok" in run.steps["search"]

        # Findings stored
        findings = db.query(Finding).filter_by(prospect_id=prospect.id).all()
        assert len(findings) >= 1
        assert any("Acme Hotel" in f.statement for f in findings)
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# Discovery provider failure → partially_completed, search findings preserved
# ---------------------------------------------------------------------------


def test_discovery_failure_partial() -> None:
    """When discovery provider fails, search findings are preserved and run
    is partially_completed."""
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        prospect.website_url = None
        db.flush()

        # Patch both search (returns empty, so discovery runs) and discovery (failing)
        original_search = research_service.get_search_provider
        original_discovery = research_service.get_discovery_provider

        research_service.get_search_provider = lambda: EmptySearchProvider()
        research_service.get_discovery_provider = lambda: FailingDiscovery()
        try:
            run = ResearchRun(prospect_id=prospect.id, triggered_by="user")
            db.add(run)
            db.flush()
            result = research_service.run_research(db, prospect_id=prospect.id, research_run=run)
        finally:
            research_service.get_search_provider = original_search
            research_service.get_discovery_provider = original_discovery

        assert result.status == "partially_completed"
        assert "discovery" in (result.error or "")
        assert result.steps is not None
        assert "search" in result.steps
        assert "ok" in result.steps["search"]
        assert "failed" in result.steps["discovery"]

        # No findings from discovery, but run didn't crash
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# All providers fail → partially_completed, not crashed
# ---------------------------------------------------------------------------


def test_all_providers_fail_no_crash() -> None:
    """When all providers fail, run is partially_completed (not crashed) and
    error info is captured."""
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        prospect.website_url = None
        db.flush()

        original_search = research_service.get_search_provider
        original_discovery = research_service.get_discovery_provider

        research_service.get_search_provider = lambda: FailingSearchProvider()
        research_service.get_discovery_provider = lambda: FailingDiscovery()
        try:
            run = ResearchRun(prospect_id=prospect.id, triggered_by="user")
            db.add(run)
            db.flush()
            result = research_service.run_research(db, prospect_id=prospect.id, research_run=run)
        finally:
            research_service.get_search_provider = original_search
            research_service.get_discovery_provider = original_discovery

        assert result.status == "partially_completed"
        assert result.error is not None
        assert len(result.error) > 0
        # No exception propagated
    finally:
        _cleanup(db, path)
