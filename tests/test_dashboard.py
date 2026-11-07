"""Dashboard tests: pipeline strip counts (UI/UX spec §2.1).

Uses the same temp-file SQLite pattern as test_phase1.py.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import Prospect
from app.services.dashboard import PIPELINE_STAGES, stage_counts, stage_label, total_prospects


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    return db, path


def _client_with_db(db: Session) -> TestClient:
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _cleanup(db: Session, path: str) -> None:
    db.close()
    app.dependency_overrides.clear()
    with suppress(OSError):
        os.unlink(path)


def _add(db: Session, name: str, stage: str, *, archived: bool = False) -> Prospect:
    p = Prospect(
        business_name=name,
        stage=stage,
        archived_at=datetime.now(UTC) if archived else None,
    )
    db.add(p)
    db.commit()
    return p


def test_stage_counts_zero_fills_and_excludes_archived() -> None:
    db, path = _make_db()
    try:
        _add(db, "A", "new")
        _add(db, "B", "new")
        _add(db, "C", "contacted")
        _add(db, "D", "converted")
        _add(db, "E", "new", archived=True)  # must not count
        _add(db, "F", "archived", archived=True)  # must not count

        counts = stage_counts(db)

        # Every pipeline stage is present and zero-filled.
        assert tuple(counts.keys()) == PIPELINE_STAGES
        assert counts["new"] == 2
        assert counts["contacted"] == 1
        assert counts["converted"] == 1
        assert counts["researched"] == 0
        # Terminal/closed states are not pipeline stages.
        assert "archived" not in counts
        assert "do_not_contact" not in counts
        assert total_prospects(db) == 4
    finally:
        _cleanup(db, path)


def test_dashboard_renders_pipeline_strip_without_fake_metrics() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        _add(db, "Acme Hotel", "researched")

        r = client.get("/")
        assert r.status_code == 200
        # Pipeline strip present, linked per stage.
        assert 'class="pipeline-strip"' in r.text
        assert "/prospects?stage=opportunity_identified" in r.text
        # The stage counts are real, not hardcoded zeros.
        assert "Acme" not in r.text  # names are not dashboard content
        # Old fake metric cards are gone.
        assert "Needs attention" not in r.text
        assert "metric-card" not in r.text
    finally:
        _cleanup(db, path)


def test_dashboard_empty_state_offers_setup_actions() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)

        r = client.get("/")
        assert r.status_code == 200
        assert "No prospects yet" in r.text
        assert "/prospects/new" in r.text
        assert 'class="pipeline-strip"' not in r.text
    finally:
        _cleanup(db, path)


def test_stage_label_is_readable() -> None:
    assert stage_label("opportunity_identified") == "Opportunity"
    assert stage_label("follow_up_due") == "Follow-up"
