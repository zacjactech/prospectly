"""Phase 5 performance sanity tests.

Verifies the 5,000-prospect list query stays under 200ms locally
(Phase 5 exit criterion) and that indexes are used (EXPLAIN QUERY PLAN).
"""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import suppress

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import Prospect


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    return db, path


def _seed_prospects(db: Session, count: int = 5000) -> None:
    """Seed `count` prospects with varied stages for list-query testing."""
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
    for i in range(count):
        db.add(
            Prospect(
                business_name=f"Prospect {i:05d}",
                normalized_name=f"prospect-{i:05d}",
                stage=stages[i % len(stages)],
                category=f"category-{i % 10}",
                location_city=f"city-{i % 20}",
                phone_e164=f"1{i:010d}" if i % 3 == 0 else None,
            )
        )
    db.flush()


def test_5000_prospect_list_under_200ms() -> None:
    """Listing 5,000 prospects must complete in <200ms (Phase 5 exit criterion)."""
    db, path = _make_db()
    try:
        _seed_prospects(db, 5000)

        from fastapi.testclient import TestClient

        def override_get_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        client.get("/")  # prime CSRF cookie

        start = time.perf_counter()
        r = client.get("/prospects?include_archived=true", follow_redirects=False)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert r.status_code == 200, r.text
        assert elapsed_ms < 200, f"List query took {elapsed_ms:.0f}ms, expected <200ms"
    finally:
        db.close()
        app.dependency_overrides.pop(get_db, None)
        with suppress(OSError):
            os.unlink(path)


def test_list_query_uses_index() -> None:
    """The prospects list query should use an index (not a full table scan)."""
    db, path = _make_db()
    try:
        _seed_prospects(db, 5000)

        # EXPLAIN QUERY PLAN on the list query
        rows = db.execute(
            text(
                "EXPLAIN QUERY PLAN "
                "SELECT * FROM prospects "
                "WHERE archived_at IS NULL "
                "ORDER BY updated_at DESC LIMIT 50"
            )
        ).all()
        plan = " ".join(str(r) for r in rows)
        # Should use an index, not a full table scan
        assert "SCAN" not in plan.upper() or "INDEX" in plan.upper(), f"Plan: {plan}"
    finally:
        db.close()
        with suppress(OSError):
            os.unlink(path)


def test_stage_count_query_uses_index() -> None:
    """The dashboard stage_counts query should use the partial index on stage."""
    db, path = _make_db()
    try:
        _seed_prospects(db, 5000)

        rows = db.execute(
            text(
                "EXPLAIN QUERY PLAN "
                "SELECT stage, count(*) FROM prospects "
                "WHERE archived_at IS NULL GROUP BY stage"
            )
        ).all()
        plan = " ".join(str(r) for r in rows)
        assert "INDEX" in plan.upper(), f"Plan should use index: {plan}"
    finally:
        db.close()
        with suppress(OSError):
            os.unlink(path)
