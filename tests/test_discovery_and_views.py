"""Regression tests for Round-3 fixes: discovery trigger (H6), saved views
apply + filter validation (M3), follow-up service guards (M4).

- H6: Flow 1 had no trigger — `queue_discover_job` had zero callers, so
  "Run discovery" was unreachable despite README/QA checklist claims.
- M3: saved views stored arbitrary JSON nothing could consume; filters are
  now whitelisted and applicable from the saved-views page and the list.
- M4: complete/cancel lived inline in both routes (no service, no guards):
  re-completing re-stamped completed_at, duplicated or dropped activities.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.jobs import worker
from app.main import app
from app.models import DiscoveryJob, FollowUp, Job, Prospect, Source
from app.services import outreach as outreach_service
from app.services import profile as profile_service
from app.services import prospect as prospect_service
from app.services import saved_views as saved_views_service
from tests.conftest import post_form


def _make_engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
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


@pytest.fixture()
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def _client(db: Session) -> TestClient:
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    client.get("/")  # prime CSRF cookie
    return client


def _cleanup() -> None:
    app.dependency_overrides.clear()


def _add_prospect(db: Session, *, name: str = "View Test Co") -> Prospect:
    source = db.query(Source).filter(Source.type == "manual").first()
    if source is None:
        source = Source(type="manual", label="Manual add", detail={})
        db.add(source)
        db.flush()
    prospect, _ = prospect_service.create_prospect(
        db,
        business_name=name,
        source=source,
        actor="user",
        skip_dupe_check=True,
    )
    assert prospect is not None
    return prospect


# ---------------------------------------------------------------------------
# H6 — discovery trigger (Flow 1)
# ---------------------------------------------------------------------------


def test_discovery_run_queues_job_and_discovery_row(db: Session) -> None:
    profile_service.update_profile(
        db, offer="websites", target_industries=["hotel"], target_locations=["Berlin"]
    )
    db.commit()
    client = _client(db)
    try:
        r = post_form(client, "/settings/profile/discovery/run", follow_redirects=False)
        assert r.status_code == 303, r.text

        dj = db.query(DiscoveryJob).one()
        assert dj.status == "queued"
        assert dj.profile_id is not None

        job = db.query(Job).filter_by(type="discover").one()
        assert job.status == "queued"
        assert job.payload["discovery_job_id"] == dj.id
        assert job.payload["profile_id"] == dj.profile_id
    finally:
        _cleanup()


def test_discovery_run_requires_target_industries_or_locations(db: Session) -> None:
    client = _client(db)
    try:
        r = post_form(client, "/settings/profile/discovery/run", follow_redirects=False)
        assert r.status_code == 400, r.text
        assert db.query(DiscoveryJob).count() == 0
        assert db.query(Job).filter_by(type="discover").count() == 0
    finally:
        _cleanup()


def test_profile_page_offers_discovery_and_shows_runs(db: Session) -> None:
    client = _client(db)
    try:
        r = client.get("/settings/profile")
        assert r.status_code == 200
        assert 'action="/settings/profile/discovery/run"' in r.text
        assert "Run discovery" in r.text
        assert "No discovery runs yet." in r.text
    finally:
        _cleanup()


def test_discovery_end_to_end_through_worker(
    session_factory: sessionmaker[Session], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flow 1 e2e (offline): trigger → queue → worker → candidates created."""

    class FakeSearch:
        provider_name = "fake_search"

        async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
            return [
                {"title": "Hotel Berlin Palace", "url": "https://hb.example", "snippet": ""},
                {"title": "Hotel Grunewald", "url": "https://hg.example", "snippet": ""},
            ]

    profile_service.update_profile(
        db, offer="x", target_industries=["hotel"], target_locations=["Berlin"]
    )
    db.commit()
    client = _client(db)
    try:
        r = post_form(client, "/settings/profile/discovery/run", follow_redirects=False)
        assert r.status_code == 303, r.text
        # The test's get_db override doesn't commit — the worker opens its
        # own session, so the queue must be durable first.
        db.commit()

        monkeypatch.setattr(worker, "SessionLocal", session_factory)
        monkeypatch.setattr(worker, "get_search_provider", lambda: FakeSearch())
        monkeypatch.setattr(worker, "get_discovery_provider", lambda: None)

        worker.run_worker(once=True)

        dj = db.query(DiscoveryJob).one()
        assert dj.status == "succeeded", dj.error
        assert dj.prospects_created == 2
        assert dj.candidates_found == 2

        names = {p.business_name for p in db.query(Prospect).all()}
        assert {"Hotel Berlin Palace", "Hotel Grunewald"} <= names
        job = db.query(Job).filter_by(type="discover").one()
        assert job.status == "succeeded"
    finally:
        _cleanup()


# ---------------------------------------------------------------------------
# M3 — saved views: save current list, apply, filter whitelist
# ---------------------------------------------------------------------------


def test_list_page_offers_save_view_when_filters_active(db: Session) -> None:
    client = _client(db)
    try:
        plain = client.get("/prospects")
        assert "Save this view" not in plain.text

        filtered = client.get("/prospects", params={"stage": "new", "q": "acme"})
        assert filtered.status_code == 200
        assert 'action="/settings/saved-views"' in filtered.text
        assert "Save this view" in filtered.text
        assert 'value="new"' in filtered.text
        assert 'value="acme"' in filtered.text
    finally:
        _cleanup()


def test_save_current_view_from_list_and_apply(db: Session) -> None:
    _add_prospect(db, name="Acme Hotels Ltd")
    db.commit()
    client = _client(db)
    try:
        # Save the current (structured) list filters.
        r = post_form(
            client,
            "/settings/saved-views",
            data={
                "name": "New acme",
                "stage": "new",
                "q": "acme",
                "tag": "",
                "category": "",
                "location": "",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text

        view = saved_views_service.list_saved_views(db)[0]
        assert view.filters == {"stage": "new", "q": "acme"}

        # Apply → redirect onto the list with those query params.
        r = client.get(f"/prospects/views/{view.id}/apply", follow_redirects=False)
        assert r.status_code == 303, r.text
        location = r.headers["location"]
        assert location.startswith("/prospects?")
        params = parse_qs(urlsplit(location).query)
        assert params == {"stage": ["new"], "q": ["acme"]}

        # Following the redirect actually filters the list.
        followed = client.get(location)
        assert followed.status_code == 200
        assert "Acme Hotels Ltd" in followed.text
    finally:
        _cleanup()


def test_saved_view_json_filters_whitelisted(db: Session) -> None:
    client = _client(db)
    try:
        # Unknown keys are rejected with a clear error (not silently stored).
        r = post_form(
            client,
            "/settings/saved-views",
            data={"name": "bad", "filters": '{"evil": "1", "stage": "new"}'},
            follow_redirects=False,
        )
        assert r.status_code == 400, r.text
        assert "evil" in r.text
        assert saved_views_service.list_saved_views(db) == []

        # Valid JSON is stored.
        r = post_form(
            client,
            "/settings/saved-views",
            data={"name": "ok", "filters": '{"stage": "new"}'},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text
        assert saved_views_service.list_saved_views(db)[0].filters == {"stage": "new"}
    finally:
        _cleanup()


def test_apply_strips_unknown_keys_and_handles_missing_view(db: Session) -> None:
    # Simulate a legacy row that predates validation.
    view = saved_views_service.create_saved_view(db, name="legacy", filters=None)

    view.filters = {"stage": "new", "evil": "x"}  # type: ignore[assignment]
    db.flush()
    client = _client(db)
    try:
        r = client.get(f"/prospects/views/{view.id}/apply", follow_redirects=False)
        assert r.status_code == 303
        params = parse_qs(urlsplit(r.headers["location"]).query)
        assert params == {"stage": ["new"]}

        r = client.get("/prospects/views/999999/apply", follow_redirects=False)
        assert r.status_code == 303
        from urllib.parse import unquote

        assert "not found" in unquote(r.headers["location"])
    finally:
        _cleanup()


# ---------------------------------------------------------------------------
# M4 — follow-up complete/cancel through the service (both surfaces)
# ---------------------------------------------------------------------------


def _open_follow_up(db: Session) -> FollowUp:
    prospect = _add_prospect(db, name=f"FollowUp Co {os.urandom(4).hex()}")
    follow_up, error = outreach_service.create_follow_up(
        db,
        prospect_id=prospect.id,
        due_date=datetime.now(UTC) + timedelta(days=3),
        note="call them",
    )
    assert error is None and follow_up is not None
    return follow_up


def test_html_complete_is_idempotent_and_logged(db: Session) -> None:
    follow_up = _open_follow_up(db)
    db.commit()
    client = _client(db)
    try:
        r = post_form(client, f"/follow-ups/{follow_up.id}/complete", follow_redirects=False)
        assert r.status_code == 303, r.text
        db.expire_all()
        stored = db.get(FollowUp, follow_up.id)
        assert stored is not None
        assert stored.status == "done"
        completed_at = stored.completed_at
        assert completed_at is not None

        # Re-complete → 409, no re-stamp, no duplicate activity.
        r = post_form(client, f"/follow-ups/{follow_up.id}/complete", follow_redirects=False)
        assert r.status_code == 409, r.text
        db.expire_all()
        stored = db.get(FollowUp, follow_up.id)
        assert stored is not None
        assert stored.completed_at == completed_at
        from app.models import Activity

        completions = (
            db.query(Activity)
            .filter_by(prospect_id=stored.prospect_id, type="follow_up_completed")
            .all()
        )
        assert len(completions) == 1
    finally:
        _cleanup()


def test_api_cancel_logs_activity_and_guards_repeats(db: Session) -> None:
    follow_up = _open_follow_up(db)
    db.commit()
    client = _client(db)
    try:
        r = client.post(
            f"/api/v1/follow-ups/{follow_up.id}/cancel",
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"

        r = client.post(
            f"/api/v1/follow-ups/{follow_up.id}/cancel",
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 409, r.text

        from app.models import Activity

        stored = db.get(FollowUp, follow_up.id)
        assert stored is not None
        cancellations = (
            db.query(Activity)
            .filter_by(prospect_id=stored.prospect_id, type="follow_up_cancelled")
            .all()
        )
        assert len(cancellations) == 1, "API cancel must log an activity (QA D12)"
    finally:
        _cleanup()


def test_follow_up_404_on_both_surfaces(db: Session) -> None:
    client = _client(db)
    try:
        r = post_form(client, "/follow-ups/999999/complete", follow_redirects=False)
        assert r.status_code == 404, r.text
        r = client.post(
            "/api/v1/follow-ups/999999/complete",
            headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
        )
        assert r.status_code == 404, r.text
    finally:
        _cleanup()
