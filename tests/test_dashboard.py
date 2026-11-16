"""Dashboard/Status-card tests: real pipeline counts on the canvas (§V2.3).

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


def test_status_card_renders_metrics_without_pipeline_strip() -> None:
    db, path = _make_db()
    try:
        client = _client_with_db(db)
        _add(db, "Acme Hotel", "researched")

        r = client.get("/")
        assert r.status_code == 200
        # Deep-link contract (§V2.2): the canvas shell with Status pre-opened.
        assert 'id="canvas-screen"' in r.text
        assert 'data-panel-open="status"' in r.text
        # §V2.3: the old pipeline strip is intentionally dropped — the board
        # carries phase chips and per-stage counts instead.
        assert 'class="pipeline-strip"' not in r.text
        assert 'data-zone="new"' in r.text
        # Metrics are real (the prospect appears on the board, and the
        # prerequisites/quick-start content is present).
        assert "Acme Hotel" in r.text
        assert 'class="metric-groups"' in r.text
        assert "Needs attention" in r.text
        assert "/settings/profile" in r.text
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
