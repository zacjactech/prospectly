"""Regression tests for the verification-round defects (QA D1-D5, reviewer N1-N3).

- D1: `ux_outreach_messages_active` must be PARTIAL (one *active* message
  per prospect) — the non-partial unique index made every second draft and
  "start new outreach" crash with IntegrityError.
- D2: every template must compile — three Phase-6+ templates shipped with
  unclosed `{% if %}` and their pages 500'd (caught by a compile sweep,
  which this test automates).
- D3: clearing suppression with an empty reason → 400 (not FastAPI's 422).
- D4: Flow 6 needs a reachable reply-recording surface (HTML form + the
  documented `POST /prospects/{id}/replies` API endpoint).
- N1: merging prospects that share an identical idempotent finding must not
  violate `ux_findings_prospect_key`.
- N3: merging a suppressed duplicate must propagate the opt-out.
- N2: the worker survives transient claim failures; the claim is a
  conditional UPDATE.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import TemplateSyntaxError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.jobs import worker
from app.main import app
from app.models import Finding, OutreachMessage, Prospect, ResearchRun, Source
from app.routes.pages import templates
from app.services import merge_service
from app.services import outreach as outreach_service
from app.services import prospect as prospect_service
from app.services import research as research_service
from tests.conftest import post_form


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
    client.get("/")  # prime CSRF cookie
    return client


def _cleanup(db: Session, path: str) -> None:
    db.close()
    app.dependency_overrides.clear()
    with suppress(OSError):
        os.unlink(path)


def _add_prospect(db: Session, *, name: str, phone: str | None = None) -> Prospect:
    source = db.query(Source).filter(Source.type == "manual").first()
    if source is None:
        source = Source(type="manual", label="Manual add", detail={})
        db.add(source)
        db.flush()
    prospect, _ = prospect_service.create_prospect(
        db,
        business_name=name,
        source=source,
        phone_e164=phone,
        phone_raw=phone,
        actor="user",
        skip_dupe_check=True,
    )
    assert prospect is not None
    return prospect


# ---------------------------------------------------------------------------
# D2 — every template must compile
# ---------------------------------------------------------------------------


def test_all_templates_compile() -> None:
    """A compile sweep over every template — catches unclosed blocks that
    only explode at render time (QA D2 shipped three such pages)."""
    root = Path(templates.env.loader.searchpath[0])  # type: ignore[union-attr]
    files = sorted(root.rglob("*.html"))
    assert files, "no templates found"
    errors: list[str] = []
    for path in files:
        try:
            templates.env.parse(path.read_text(encoding="utf-8"))
        except TemplateSyntaxError as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
    assert not errors, "templates failed to compile:\n" + "\n".join(errors)


def test_previously_broken_pages_render() -> None:
    """The three Phase-6+ pages that returned 500 must now render."""
    db, path = _make_db()
    try:
        client = _client(db)
        for url, marker in (
            ("/settings/saved-views", "Saved Views"),
            ("/settings/retention", "Retention Sweep"),
            ("/merge/duplicates", "Merge"),
        ):
            r = client.get(url)
            assert r.status_code == 200, f"{url}: {r.status_code} {r.text[:300]}"
            assert marker in r.text, url
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# D1 — second message versions are allowed (partial index)
# ---------------------------------------------------------------------------


def test_second_draft_and_start_new_outreach_work() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, name="Second Draft Co", phone="442079460958")

        m1, error = outreach_service.create_draft(db, prospect_id=prospect.id, current_text="first")
        assert error is None and m1 is not None
        m2, error = outreach_service.create_draft(
            db, prospect_id=prospect.id, current_text="second"
        )
        assert error is None, error
        assert m2 is not None
        assert m1.active is False and m2.active is True

        # Full send chain, then start the next version (Flow 7 guard).
        m2, error = outreach_service.submit_message(
            db, message_id=m2.id, current_text=m2.current_text
        )
        assert error is None
        m2, error = outreach_service.approve_message(
            db, message_id=m2.id, ungrounded_acknowledgement=True
        )
        assert error is None
        m2, error, url = outreach_service.open_message(db, message_id=m2.id)
        assert error is None and url is not None
        m2, error = outreach_service.confirm_sent(db, message_id=m2.id)
        assert error is None

        m3, error = outreach_service.start_new_outreach(db, prospect_id=prospect.id)
        assert error is None, error
        assert m3 is not None and m3.active is True and m3.status == "draft"
        db.commit()

        actives = db.query(OutreachMessage).filter_by(prospect_id=prospect.id, active=True).count()
        assert actives == 1, "exactly one ACTIVE message per prospect (§2.9)"
    finally:
        _cleanup(db, path)


def test_html_generate_draft_twice_does_not_500() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, name="Twice Gen Co")
        db.commit()
        client = _client(db)
        r = post_form(client, f"/prospects/{prospect.id}/draft/generate", follow_redirects=False)
        assert r.status_code == 303, r.text
        r = post_form(client, f"/prospects/{prospect.id}/draft/generate", follow_redirects=False)
        assert r.status_code == 303, f"second generate crashed: {r.status_code}"
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# D3 / D4 — suppression clear status code, reply surfaces
# ---------------------------------------------------------------------------


def test_clear_suppression_with_empty_reason_returns_400() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, name="Clear Reason Co")
        prospect_service.set_do_not_contact(db, prospect.id, reason="opt-out")
        db.commit()
        client = _client(db)

        r = post_form(
            client,
            f"/prospects/{prospect.id}/do-not-contact/clear",
            data={"reason": ""},
            follow_redirects=False,
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
    finally:
        _cleanup(db, path)


def test_detail_page_has_reply_form_and_html_reply_works() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, name="Reply Form Co")
        db.commit()
        client = _client(db)

        r = client.get(f"/prospects/{prospect.id}")
        assert r.status_code == 200
        assert f'action="/prospects/{prospect.id}/reply"' in r.text

        r = post_form(
            client,
            f"/prospects/{prospect.id}/reply",
            data={"text": "Yes, send a quote"},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text
        db.expire_all()
        updated = db.get(Prospect, prospect.id)
        assert updated is not None and updated.stage == "replied"
    finally:
        _cleanup(db, path)


def test_api_reply_endpoint_matches_api_design() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, name="Reply API Co")
        db.commit()
        client = _client(db)
        headers = {"X-CSRF-Token": client.cookies.get("csrf_token", "")}

        r = client.post(
            f"/api/v1/prospects/{prospect.id}/replies",
            json={"text": "interested"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["stage"] == "replied"

        # Empty text → 400; suppressed prospect → 403 (API_DESIGN §2.6).
        r = client.post(
            f"/api/v1/prospects/{prospect.id}/replies",
            json={"text": "   "},
            headers=headers,
        )
        assert r.status_code == 400, r.text

        other = _add_prospect(db, name="Reply API Suppressed")
        prospect_service.set_do_not_contact(db, other.id, reason="stop")
        db.commit()
        r = client.post(
            f"/api/v1/prospects/{other.id}/replies",
            json={"text": "hello?"},
            headers=headers,
        )
        assert r.status_code == 403, r.text
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# N1 / N3 — merge correctness
# ---------------------------------------------------------------------------


def test_merge_with_identical_findings_does_not_crash() -> None:
    db, path = _make_db()
    try:
        source = _add_prospect(db, name="Merge Twin A")
        target = _add_prospect(db, name="Merge Twin B")
        # Both researched no-URL prospects carry the same idempotent finding.
        for prospect in (source, target):
            research_service._upsert_finding(
                db,
                prospect_id=prospect.id,
                research_run_id=None,
                kind="website",
                statement="No website URL recorded for this prospect",
                source_type="user",
                verification_status="verified",
                confidence="high",
                origin="human",
            )
        db.flush()

        merged, error = merge_service.merge_prospects(db, source_id=source.id, target_id=target.id)
        assert error is None, error
        assert merged is not None and merged.id == target.id
        db.commit()

        surviving = [
            f
            for f in db.query(Finding).filter_by(prospect_id=target.id).all()
            if f.statement == "No website URL recorded for this prospect"
        ]
        assert len(surviving) == 1, "identical finding must not be duplicated"
        db.expire_all()
        src = db.get(Prospect, source.id)
        assert src is not None and src.archived_at is not None
    finally:
        _cleanup(db, path)


def test_merge_propagates_suppression_from_source() -> None:
    db, path = _make_db()
    try:
        source = _add_prospect(db, name="Suppressed Dup")
        target = _add_prospect(db, name="Live Target")
        target_stage_before = target.stage
        prospect_service.set_do_not_contact(db, source.id, reason="opt-out on WhatsApp")
        db.flush()

        merged, error = merge_service.merge_prospects(db, source_id=source.id, target_id=target.id)
        assert error is None, error
        db.expire_all()
        live = db.get(Prospect, target.id)
        assert live is not None
        assert live.do_not_contact is True, "opt-out must travel with the merge (P10)"
        assert live.do_not_contact_reason == "opt-out on WhatsApp"
        assert live.stage == "do_not_contact"

        # …and clearing restores the target's pre-merge stage.
        cleared, error = prospect_service.clear_do_not_contact(db, target.id, reason="re-confirmed")
        assert error is None, error
        db.expire_all()
        live = db.get(Prospect, target.id)
        assert live is not None
        assert live.do_not_contact is False
        assert live.stage == target_stage_before
        assert merged is not None
    finally:
        _cleanup(db, path)


def test_merge_keeps_single_active_message() -> None:
    db, path = _make_db()
    try:
        source = _add_prospect(db, name="Msg Dup A", phone="442079460958")
        target = _add_prospect(db, name="Msg Dup B", phone="442079460959")
        for prospect in (source, target):
            message, error = outreach_service.create_draft(
                db, prospect_id=prospect.id, current_text=f"draft {prospect.id}"
            )
            assert error is None and message is not None
        db.flush()

        _, error = merge_service.merge_prospects(db, source_id=source.id, target_id=target.id)
        assert error is None, error
        db.commit()

        actives = [
            m for m in db.query(OutreachMessage).filter_by(prospect_id=target.id).all() if m.active
        ]
        assert len(actives) == 1, "§2.9: one active message survives the merge"
    finally:
        _cleanup(db, path)


# ---------------------------------------------------------------------------
# N2 — worker claim resilience
# ---------------------------------------------------------------------------


def test_worker_survives_claim_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{tmp}")
    Base.metadata.create_all(engine)
    try:
        # Isolate from the real default database — claim never runs, but the
        # loop still opens a session.
        monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=engine))

        def exploding_claim(_db):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(worker, "claim_job", exploding_claim)
        # Must not raise — a transient claim failure must not kill the worker.
        worker.run_worker(once=True)
    finally:
        engine.dispose()
        with suppress(OSError):
            os.unlink(tmp)


def test_claim_is_conditional_and_counts_attempts() -> None:
    db, path = _make_db()
    try:
        prospect = _add_prospect(db, name="Claim Test Co")
        run = ResearchRun(prospect_id=prospect.id, triggered_by="user")
        db.add(run)
        db.flush()
        job = worker.queue_research_job(db, prospect_id=prospect.id, research_run_id=run.id)
        db.commit()

        first = worker.claim_job(db)
        assert first is not None and first.id == job.id
        assert first.status == "running" and first.attempts == 1
        db.commit()

        # Second claim (fresh state) finds nothing claimable.
        second = worker.claim_job(db)
        assert second is None
    finally:
        _cleanup(db, path)


def test_templates_have_no_inline_event_handlers() -> None:
    """CSP `script-src 'self'` forbids inline handlers (main.py, UI spec).

    A real-browser pass caught `base.html` shipping an `onclick` that the CSP
    silently blocked — the help backdrop looked wired up but was dead. This
    makes the whole class of defect impossible to re-ship.
    """
    import re

    root = Path(templates.env.loader.searchpath[0])  # type: ignore[union-attr]
    pattern = re.compile(
        r"\son(click|change|submit|load|focus|blur|input|keydown|keyup|dblclick|mouseover)\s*="
    )
    offenders: list[str] = []
    for path in sorted(root.rglob("*.html")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(root)}:{lineno}")
    assert not offenders, "inline event handlers blocked by CSP:\n" + "\n".join(offenders)


def test_submit_for_review_forms_carry_current_text() -> None:
    """Regression (2026-11-11 visual audit): the "Submit for review" forms on
    the prospect detail and message edit pages omitted the required
    ``current_text`` field, so every UI submit returned FastAPI's 422 —
    only the service layer was exercised by the suite. Both forms must
    render the field, and the post exactly as rendered must transition
    draft → pending_review."""
    import re

    db, path = _make_db()
    try:
        prospect = _add_prospect(db, name="Submit Form Co", phone="442079460958")
        msg, error = outreach_service.create_draft(
            db, prospect_id=prospect.id, current_text="hello there"
        )
        assert error is None and msg is not None
        db.commit()

        client = _client(db)
        for url in (f"/prospects/{prospect.id}", f"/messages/{msg.id}/edit"):
            html = client.get(url).text
            m = re.search(
                r'<form[^>]*action="/messages/\d+/submit"[^>]*>(.*?)</form>',
                html,
                re.S,
            )
            assert m, f"no Submit-for-review form rendered at {url}"
            assert 'name="current_text"' in m.group(1), (
                f"submit form at {url} omits current_text (422 in the browser)"
            )

        r = post_form(
            client,
            f"/messages/{msg.id}/submit",
            {"current_text": msg.current_text},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303, 307), f"{r.status_code}: {r.text[:200]}"
        db.refresh(msg)
        assert msg.status == "pending_review"
    finally:
        _cleanup(db, path)
