"""Daily cap enforcement (COST_ANALYSIS, review finding #15).

Before this change the caps were decorative: declared in config, shown in the
UI, referenced nowhere. These tests prove the hard stops at both ends:

- a keyed search provider at/over its cap is **not called**, the step is
  reported as skipped, and the block is counted as rate-limited;
- a skipped search is never evidence for ``no_website_found`` (P6);
- uncapped (unmetered) sources are never blocked;
- the AI cap blocks generation before any call, while the template floor is
  never capped (it makes no external call);
- ``ai_usage`` is written for successful *and* failed AI calls.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import AIError
from app.ai.generator import GeneratedDraft
from app.config import settings
from app.db import Base
from app.models import AIUsage, Finding, Prospect, ProviderUsage, ResearchRun
from app.services import outreach as outreach_service
from app.services import research as research_service
from app.services.usage import ai_calls_today, usage_summary


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal(), path


def _cleanup(db: Session, path: str) -> None:
    db.close()
    with suppress(OSError):
        os.unlink(path)


def _add_prospect(db: Session, name: str = "Acme Hotel") -> Prospect:
    prospect = Prospect(business_name=name, normalized_name=name.lower(), stage="outreach_ready")
    db.add(prospect)
    db.flush()
    return prospect


def _add_finding(db: Session, prospect_id: int, statement: str = "No website found") -> Finding:
    import hashlib
    from datetime import UTC, datetime

    finding = Finding(
        prospect_id=prospect_id,
        kind="website",
        statement=statement,
        source_type="user",
        origin="human",
        verification_status="verified",
        confidence="high",
        retrieved_at=datetime.now(UTC),
        finding_key=hashlib.sha256(statement.encode()).hexdigest()[:32],
    )
    db.add(finding)
    db.flush()
    return finding


class CountingSearch:
    """Search provider that records how often it was actually called."""

    def __init__(self, provider_name: str = "tavily") -> None:
        self.provider_name = provider_name
        self.calls = 0

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        self.calls += 1
        return []


def _run_research(db: Session, prospect, search) -> ResearchRun:
    orig_search = research_service.get_search_provider
    orig_disc = research_service.get_discovery_provider
    research_service.get_search_provider = lambda: search
    research_service.get_discovery_provider = lambda: None
    try:
        run = ResearchRun(prospect_id=prospect.id, triggered_by="user")
        db.add(run)
        db.flush()
        return research_service.run_research(db, prospect_id=prospect.id, research_run=run)
    finally:
        research_service.get_search_provider = orig_search
        research_service.get_discovery_provider = orig_disc


# ---------------------------------------------------------------------------
# Search provider caps
# ---------------------------------------------------------------------------


def test_cap_reached_skips_the_call_and_counts_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "tavily_daily_cap", 1)
    db, path = _make_db()
    try:
        db.add(ProviderUsage(provider="tavily", kind="search", calls=1, period=_today()))
        db.flush()
        prospect = _add_prospect(db)
        search = CountingSearch("tavily")

        run = _run_research(db, prospect, search)

        assert search.calls == 0, "capped provider must not be called"
        assert run.steps["search"].startswith("skipped (daily cap reached")
        row = db.query(ProviderUsage).filter_by(provider="tavily").one()
        assert row.calls == 1, "a blocked call adds no calls"
        assert row.rate_limited == 1
    finally:
        _cleanup(db, path)


def test_cap_zero_blocks_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tavily_daily_cap", 0)
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        search = CountingSearch("tavily")
        run = _run_research(db, prospect, search)
        assert search.calls == 0
        assert "skipped (daily cap reached" in run.steps["search"]
    finally:
        _cleanup(db, path)


def test_under_cap_calls_provider_and_accumulates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tavily_daily_cap", 5)
    db, path = _make_db()
    try:
        db.add(ProviderUsage(provider="tavily", kind="search", calls=2, period=_today()))
        db.flush()
        prospect = _add_prospect(db)
        search = CountingSearch("tavily")

        run = _run_research(db, prospect, search)

        assert search.calls == 1
        assert run.steps["search"] == "ok (no results)"
        row = db.query(ProviderUsage).filter_by(provider="tavily").one()
        assert row.calls == 3
        assert row.rate_limited == 0
    finally:
        _cleanup(db, path)


def test_unmetered_provider_is_never_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """ddgs/osm/bizdata have no declared cap — usage rows cannot block them."""
    monkeypatch.setattr(settings, "tavily_daily_cap", 1)
    db, path = _make_db()
    try:
        db.add(ProviderUsage(provider="ddgs", kind="search", calls=99999, period=_today()))
        db.flush()
        prospect = _add_prospect(db)
        search = CountingSearch("ddgs")

        run = _run_research(db, prospect, search)

        assert search.calls == 1
        assert run.steps["search"] == "ok (no results)"
    finally:
        _cleanup(db, path)


def test_capped_search_is_not_evidence_for_no_website(monkeypatch: pytest.MonkeyPatch) -> None:
    """P6: a skipped search must never produce `no_website_found`."""
    monkeypatch.setattr(settings, "tavily_daily_cap", 0)
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)  # no website URL recorded
        run = _run_research(db, prospect, CountingSearch("tavily"))

        db.expire_all()
        updated = db.get(Prospect, prospect.id)
        assert run.steps["search"].startswith("skipped")
        assert updated.website_status == "check_failed"
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# AI cap + ai_usage accounting
# ---------------------------------------------------------------------------


class FakeGenerator:
    provider_name = "gemini"
    model = "gemini-3.5-flash"

    def __init__(
        self, *, draft: GeneratedDraft | None = None, error: AIError | None = None
    ) -> None:
        self.calls = 0
        self._draft = draft
        self._error = error

    async def generate(self, ctx):
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._draft is not None
        return self._draft


def _patch_generator(monkeypatch: pytest.MonkeyPatch, generator) -> None:
    monkeypatch.setattr(outreach_service, "get_generator", lambda: generator)


def test_ai_cap_blocks_generation_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_daily_cap", 1)
    db, path = _make_db()
    try:
        db.add(
            AIUsage(
                provider="gemini", model="gemini-3.5-flash", task="draft", calls=1, period=_today()
            )
        )
        db.flush()
        prospect = _add_prospect(db)
        _add_finding(db, prospect.id)
        generator = FakeGenerator(draft=GeneratedDraft(text="x", origin="ai", grounding=[]))
        _patch_generator(monkeypatch, generator)

        message, error = outreach_service.generate_draft(db, prospect_id=prospect.id)

        assert message is None
        assert error is not None and error.startswith("cap:")
        assert generator.calls == 0
        assert ai_calls_today(db) == 1, "a blocked generation adds no AI calls"
    finally:
        _cleanup(db, path)


def test_ai_cap_zero_blocks_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_daily_cap", 0)
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        _add_finding(db, prospect.id)
        generator = FakeGenerator(draft=GeneratedDraft(text="x", origin="ai", grounding=[]))
        _patch_generator(monkeypatch, generator)
        _, error = outreach_service.generate_draft(db, prospect_id=prospect.id)
        assert error is not None and error.startswith("cap:")
        assert generator.calls == 0
    finally:
        _cleanup(db, path)


def test_template_floor_is_never_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero external calls ⇒ the cap must not stop the product working (P11)."""
    monkeypatch.setattr(settings, "ai_daily_cap", 0)
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding = _add_finding(db, prospect.id)
        message, error = outreach_service.generate_draft(db, prospect_id=prospect.id)
        assert error is None
        assert message is not None and message.origin == "template"
        assert message.grounding == [finding.id]
        assert ai_calls_today(db) == 0
    finally:
        _cleanup(db, path)


def test_successful_ai_generation_records_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        finding = _add_finding(db, prospect.id)
        draft = GeneratedDraft(
            text="Hi Acme, I noticed you have no website. Worth a chat?",
            origin="ai",
            grounding=[finding.id],
            meta={
                "task": "draft",
                "calls": 2,
                "ok_calls": 1,
                "failed_calls": 1,
                "prompt_tokens": 120,
                "completion_tokens": 45,
            },
        )
        _patch_generator(monkeypatch, FakeGenerator(draft=draft))

        message, error = outreach_service.generate_draft(db, prospect_id=prospect.id)

        assert error is None and message is not None
        assert message.origin == "ai"
        row = db.query(AIUsage).one()
        assert (row.provider, row.model, row.task) == ("gemini", "gemini-3.5-flash", "draft")
        assert row.calls == 2
        assert row.ok_calls == 1
        assert row.failed_calls == 1
        assert row.prompt_tokens == 120
        assert row.completion_tokens == 45
    finally:
        _cleanup(db, path)


def test_failed_ai_generation_records_spend_and_surfaces_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        _add_finding(db, prospect.id)
        error = AIError(
            "gemini returned invalid output after one repair retry: bad json",
            provider="gemini",
            model="gemini-3.5-flash",
            calls=2,
            failed_calls=2,
            prompt_tokens=80,
            completion_tokens=10,
        )
        _patch_generator(monkeypatch, FakeGenerator(error=error))

        message, err = outreach_service.generate_draft(db, prospect_id=prospect.id)

        assert message is None
        assert err is not None and err.startswith("ai:")
        assert "repair retry" in err
        row = db.query(AIUsage).one()
        assert row.failed_calls == 2
        assert row.ok_calls == 0
        assert row.prompt_tokens == 80
    finally:
        _cleanup(db, path)


def test_usage_summary_reports_caps_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tavily_daily_cap", 2)
    monkeypatch.setattr(settings, "ai_daily_cap", 3)
    db, path = _make_db()
    try:
        db.add(ProviderUsage(provider="tavily", kind="search", calls=2, period=_today()))
        db.add(AIUsage(provider="gemini", model="m", task="draft", calls=1, period=_today()))
        db.flush()

        summary = usage_summary(db)

        assert summary["tavily"] == {"cap": 2, "used": 2, "reached": True}
        assert summary["serper"]["used"] == 0 and summary["serper"]["reached"] is False
        assert summary["ai"] == {"cap": 3, "used": 1, "reached": False}
    finally:
        _cleanup(db, path)


def _today():
    from datetime import date

    return date.today()
