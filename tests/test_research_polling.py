"""HTMX research-progress polling tests (SYSTEM_ARCHITECTURE §4.2, API_DESIGN §2.3).

The research page polls ``/prospects/{id}/research/status`` every 2 seconds while
the latest run is ``running``; a terminal run renders the same fragment without
``hx-*`` attributes so the outerHTML swap stops polling by itself. Findings are
part of the fragment so a poll refreshes them too.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import BASE_DIR
from app.db import Base, get_db
from app.main import app
from app.models import Finding, Prospect, ResearchRun
from app.services import research as research_service


def _make_db() -> tuple[Session, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    return db, path


def _client(db: Session) -> TestClient:
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    client.get("/")
    return client


def _cleanup(db: Session, path: str) -> None:
    db.close()
    app.dependency_overrides.clear()
    with suppress(OSError):
        os.unlink(path)


def _add_prospect(db: Session, name: str = "Polling Co") -> Prospect:
    prospect = Prospect(business_name=name, normalized_name=name.lower(), stage="new")
    db.add(prospect)
    db.flush()
    return prospect


def _add_run(db: Session, prospect: Prospect, status: str) -> ResearchRun:
    now = datetime.now(UTC)
    run = ResearchRun(
        prospect_id=prospect.id,
        status=status,
        triggered_by="user",
        started_at=now if status in ("running", "succeeded", "failed") else None,
        finished_at=now if status in ("succeeded", "failed") else None,
    )
    db.add(run)
    db.flush()
    return run


def _add_finding(db: Session, prospect: Prospect, statement: str) -> Finding:
    finding = Finding(
        prospect_id=prospect.id,
        kind="observation",
        statement=statement,
        source_type="user",
        retrieved_at=datetime.now(UTC),
        verification_status="unverified",
        confidence="low",
        origin="human",
        finding_key=research_service._finding_key("observation", statement),
    )
    db.add(finding)
    db.flush()
    return finding


# ---------------------------------------------------------------------------
# Polling attributes
# ---------------------------------------------------------------------------


def test_running_run_page_and_fragment_poll() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        _add_run(db, prospect, "running")
        db.commit()
        client = _client(db)

        url = f"/prospects/{prospect.id}/research"
        r = client.get(url)
        assert r.status_code == 200, r.text
        assert f'hx-get="/prospects/{prospect.id}/research/status"' in r.text
        assert 'hx-trigger="every 2s"' in r.text
        assert 'hx-swap="outerHTML"' in r.text
        assert "Research in progress" in r.text
        # The obsolete manual-refresh prompt is gone.
        assert "Refresh this page" not in r.text

        r = client.get(f"/prospects/{prospect.id}/research/status")
        assert r.status_code == 200, r.text
        assert f'hx-get="/prospects/{prospect.id}/research/status"' in r.text
        assert 'hx-trigger="every 2s"' in r.text
        # Fragment-only: it must not include the full page chrome.
        assert "<html" not in r.text
        assert "Research runs" not in r.text
    finally:
        _cleanup(db, path)


def test_terminal_run_stops_polling() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        _add_run(db, prospect, "succeeded")
        db.commit()
        client = _client(db)

        r = client.get(f"/prospects/{prospect.id}/research")
        assert r.status_code == 200, r.text
        assert "hx-get" not in r.text
        assert "hx-trigger" not in r.text
        assert "Research in progress" not in r.text

        r = client.get(f"/prospects/{prospect.id}/research/status")
        assert r.status_code == 200, r.text
        assert "hx-get" not in r.text
        assert "hx-trigger" not in r.text
    finally:
        _cleanup(db, path)


def test_findings_render_inside_fragment() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        _add_run(db, prospect, "running")
        _add_finding(db, prospect, "Cached evidence statement")
        db.commit()
        client = _client(db)

        r = client.get(f"/prospects/{prospect.id}/research/status")
        assert r.status_code == 200, r.text
        assert "Cached evidence statement" in r.text
        assert "Findings" in r.text
        assert "Add finding" in r.text
        # Envelope opens in the fragment so the outerHTML swap keeps the node id.
        assert 'id="research-live"' in r.text
    finally:
        _cleanup(db, path)


def test_status_route_404_for_missing_prospect() -> None:
    db, path = _make_db()
    try:
        db.commit()
        client = _client(db)
        r = client.get("/prospects/9999/research/status")
        assert r.status_code == 404, r.text
    finally:
        _cleanup(db, path)


def test_failed_run_error_is_shown_in_fragment() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db)
        run = _add_run(db, prospect, "failed")
        run.error = "search: rate limited"
        db.commit()
        client = _client(db)

        r = client.get(f"/prospects/{prospect.id}/research/status")
        assert r.status_code == 200, r.text
        assert "search: rate limited" in r.text
        assert "Research failed" in r.text
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# Vendored HTMX (D2: no npm/build step, CSP script-src 'self')
# ---------------------------------------------------------------------------


def test_htmx_is_vendored_and_loaded() -> None:
    htmx_path = BASE_DIR / "app" / "static" / "js" / "htmx.min.js"
    payload = htmx_path.read_bytes()
    assert len(payload) > 20_000, "htmx.min.js looks like a placeholder"
    assert payload.startswith((b"var htmx", b"!function", b"/*")), payload[:60]
    assert b"htmx" in payload[:200]

    db, path = _make_db()
    try:
        db.commit()
        client = _client(db)
        r = client.get("/")
        assert r.status_code == 200
        assert '<script src="/static/js/htmx.min.js" defer></script>' in r.text

        r = client.get("/static/js/htmx.min.js")
        assert r.status_code == 200
        assert len(r.content) == len(payload)
    finally:
        _cleanup(db, path)
