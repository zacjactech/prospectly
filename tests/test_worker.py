"""Worker job-processing tests (defect C1).

Every test uses a throwaway file-backed SQLite database with foreign keys
enforced, and every provider entry point is replaced by a fake — no network
calls are made (AGENTS.md §7). A fresh session is used to prove that worker
writes survive the commit boundary instead of being rolled back on close.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.jobs import worker
from app.models import Activity, Finding, Job, Prospect, ResearchRun, Source
from app.services import research as research_service
from app.services.prospect import create_prospect


class WorkingSearchProvider:
    """Search provider returning one synthetic result; never touches the network."""

    provider_name = "fake_search"

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        return [
            {
                "title": "Acme Hotel - Official Site",
                "url": "https://acme.example",
                "snippet": "Best hotel in Berlin",
            }
        ]


def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _make_engine() -> tuple[Engine, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    event.listens_for(engine, "connect")(_enable_foreign_keys)
    Base.metadata.create_all(engine)
    return engine, path


@pytest.fixture()
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine, path = _make_engine()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()
        with suppress(OSError):
            os.unlink(path)


def _add_prospect(db: Session, *, website_url: str | None = None) -> Prospect:
    source = db.query(Source).filter(Source.type == "manual").first()
    if source is None:
        source = Source(type="manual", label="Manual add", detail={})
        db.add(source)
        db.flush()
    prospect, error = create_prospect(
        db,
        business_name="Acme Hotel",
        source=source,
        website_url=website_url,
        category="hotel",
        location_city="Berlin",
        actor="user",
        skip_dupe_check=True,
    )
    assert prospect is not None, error
    return prospect


def _enqueue_research(
    db: Session, *, website_url: str | None = None
) -> tuple[Prospect, ResearchRun, Job]:
    prospect = _add_prospect(db, website_url=website_url)
    run = ResearchRun(prospect_id=prospect.id, triggered_by="system")
    db.add(run)
    db.flush()
    job = worker.queue_research_job(db, prospect_id=prospect.id, research_run_id=run.id)
    db.commit()
    return prospect, run, job


def _stub_finding(db: Session, *, prospect_id: int, run_id: int, key: str) -> None:
    db.add(
        Finding(
            prospect_id=prospect_id,
            research_run_id=run_id,
            kind="identity",
            statement="Stub finding from worker test",
            source_type="search_result",
            retrieved_at=datetime.now(UTC),
            finding_key=key,
        )
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _assert_no_zero_prospect_activity(db: Session) -> None:
    assert db.query(Activity).filter_by(prospect_id=0).count() == 0


# ---------------------------------------------------------------------------
# Persistence across the commit boundary
# ---------------------------------------------------------------------------


def test_process_one_job_persists_terminal_status_and_run(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the missing commit: a fresh session must see the result."""

    def stub_run_research(db: Session, *, prospect_id: int, research_run: ResearchRun):
        research_run.status = "succeeded"
        _stub_finding(db, prospect_id=prospect_id, run_id=research_run.id, key="stub-commit")
        db.flush()
        return research_run

    monkeypatch.setattr(worker.research_service, "run_research", stub_run_research)

    db = session_factory()
    prospect, run, job = _enqueue_research(db)
    job_id, run_id = job.id, run.id
    assert worker.process_one_job(db) is True
    db.close()

    verify = session_factory()
    try:
        stored_job = verify.get(Job, job_id)
        assert stored_job is not None
        assert stored_job.status == "succeeded"
        assert stored_job.attempts == 1

        stored_run = verify.get(ResearchRun, run_id)
        assert stored_run is not None
        assert stored_run.status == "succeeded"

        findings = verify.query(Finding).filter_by(prospect_id=prospect.id).all()
        assert len(findings) == 1
        _assert_no_zero_prospect_activity(verify)
    finally:
        verify.close()


def test_real_run_research_persists_through_worker(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the real research service against fake providers end-to-end."""
    monkeypatch.setattr(research_service, "get_search_provider", lambda: WorkingSearchProvider())
    monkeypatch.setattr(research_service, "get_discovery_provider", lambda: None)

    db = session_factory()
    prospect, run, job = _enqueue_research(db)  # no website → no network fetch
    job_id, run_id = job.id, run.id
    assert worker.process_one_job(db) is True
    db.close()

    verify = session_factory()
    try:
        stored_job = verify.get(Job, job_id)
        assert stored_job is not None
        assert stored_job.status == "succeeded"

        stored_run = verify.get(ResearchRun, run_id)
        assert stored_run is not None
        assert stored_run.status == "succeeded"
        assert stored_run.steps is not None
        assert "ok" in stored_run.steps["search"]

        findings = verify.query(Finding).filter_by(prospect_id=prospect.id).all()
        assert len(findings) >= 1
        assert any("Acme Hotel" in f.statement for f in findings)
        _assert_no_zero_prospect_activity(verify)
    finally:
        verify.close()


# ---------------------------------------------------------------------------
# Claim durability / stale reclaim
# ---------------------------------------------------------------------------


def test_no_job_commits_and_leaves_no_running_rows(
    session_factory: sessionmaker[Session],
) -> None:
    db = session_factory()
    assert worker.process_one_job(db) is False
    db.close()

    verify = session_factory()
    try:
        assert verify.query(Job).filter_by(status="running").count() == 0
    finally:
        verify.close()


def test_stale_reclaim_requeues_without_integrity_error(
    session_factory: sessionmaker[Session],
) -> None:
    old = datetime.now(UTC) - timedelta(minutes=worker.STALE_JOB_MINUTES + 5)
    future = datetime.now(UTC) + timedelta(hours=1)

    db = session_factory()
    prospect = _add_prospect(db)
    run = ResearchRun(prospect_id=prospect.id, triggered_by="system")
    db.add(run)
    db.flush()
    research_job = Job(
        type="research",
        payload={"prospect_id": prospect.id, "research_run_id": run.id},
        status="running",
        attempts=1,
        locked_at=old,
        updated_at=old,
        run_after=future,
    )
    discover_job = Job(
        type="discover",
        payload={"profile_id": 42, "discovery_job_id": None},
        status="running",
        attempts=1,
        locked_at=old,
        updated_at=old,
        run_after=future,
    )
    db.add_all([research_job, discover_job])
    db.commit()
    research_job_id, discover_job_id = research_job.id, discover_job.id
    db.close()

    worker_db = session_factory()
    try:
        # Reclaim happens; neither job is claimable while run_after is future.
        assert worker.process_one_job(worker_db) is False
    finally:
        worker_db.close()

    verify = session_factory()
    try:
        for job_id in (research_job_id, discover_job_id):
            stored = verify.get(Job, job_id)
            assert stored is not None
            assert stored.status == "queued"
            assert stored.locked_at is None
            assert stored.last_error == "Reclaimed after stale running state"

        reclaim_rows = verify.query(Activity).filter_by(type="job_reclaimed").all()
        assert len(reclaim_rows) == 1
        assert reclaim_rows[0].prospect_id == prospect.id
        _assert_no_zero_prospect_activity(verify)
    finally:
        verify.close()


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------


def _raising_research(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("provider exploded")


def test_failed_job_is_requeued_with_future_run_after(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker.research_service, "run_research", _raising_research)

    db = session_factory()
    _prospect, _run, job = _enqueue_research(db)
    job_id = job.id
    assert worker.process_one_job(db) is True
    db.close()

    verify = session_factory()
    try:
        stored = verify.get(Job, job_id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.attempts == 1
        assert stored.last_error == "provider exploded"
        assert stored.run_after is not None
        assert _as_utc(stored.run_after) > datetime.now(UTC)
        assert stored.locked_at is None
    finally:
        verify.close()


def test_job_fails_after_max_attempts(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker.research_service, "run_research", _raising_research)

    db = session_factory()
    _prospect, _run, job = _enqueue_research(db)
    job.max_attempts = 1
    db.commit()
    job_id = job.id
    assert worker.process_one_job(db) is True
    db.close()

    verify = session_factory()
    try:
        stored = verify.get(Job, job_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.attempts == 1
        assert stored.last_error == "provider exploded"
    finally:
        verify.close()


def test_unexpected_processing_exception_rolls_back_and_retries(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(_db: Session, _job: Job) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(worker, "process_job", explode)

    db = session_factory()
    _prospect, _run, job = _enqueue_research(db)
    job_id = job.id
    assert worker.process_one_job(db) is True
    db.close()

    verify = session_factory()
    try:
        stored = verify.get(Job, job_id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.attempts == 1
        assert stored.last_error == "Unexpected error: kaboom"
        assert verify.query(Job).filter_by(status="running").count() == 0
    finally:
        verify.close()


# ---------------------------------------------------------------------------
# Unknown job type
# ---------------------------------------------------------------------------


def test_unknown_job_type_fails_without_retry(
    session_factory: sessionmaker[Session],
) -> None:
    db = session_factory()
    job = Job(type="mystery", payload={}, status="queued")
    db.add(job)
    db.commit()
    job_id = job.id

    assert worker.process_one_job(db) is True
    db.close()

    verify = session_factory()
    try:
        stored = verify.get(Job, job_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.attempts == 1
        assert stored.last_error == "Unknown job type: mystery"
        assert stored.run_after is None
    finally:
        verify.close()


# ---------------------------------------------------------------------------
# run_worker(once=True)
# ---------------------------------------------------------------------------


def test_run_worker_once_processes_one_job_and_exits(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def stub_run_research(db: Session, *, prospect_id: int, research_run: ResearchRun):
        research_run.status = "succeeded"
        _stub_finding(db, prospect_id=prospect_id, run_id=research_run.id, key="stub-once")
        db.flush()
        return research_run

    monkeypatch.setattr(worker.research_service, "run_research", stub_run_research)
    monkeypatch.setattr(worker, "SessionLocal", session_factory)

    def no_sleep(_seconds: float) -> None:
        raise AssertionError("run_worker slept while --once was requested")

    monkeypatch.setattr(worker, "time", SimpleNamespace(sleep=no_sleep))

    calls = {"count": 0}
    real_process_one_job = worker.process_one_job

    def counting_process_one_job(db: Session) -> bool:
        calls["count"] += 1
        if calls["count"] > 3:
            raise AssertionError("run_worker did not stop after one iteration")
        return real_process_one_job(db)

    monkeypatch.setattr(worker, "process_one_job", counting_process_one_job)

    db = session_factory()
    _prospect, _run, job = _enqueue_research(db)
    job_id = job.id
    db.close()

    worker.run_worker(once=True)

    assert calls["count"] == 1
    verify = session_factory()
    try:
        stored = verify.get(Job, job_id)
        assert stored is not None
        assert stored.status == "succeeded"
        assert verify.query(Job).count() == 1
    finally:
        verify.close()
