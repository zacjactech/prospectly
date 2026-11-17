"""Canvas-only contract tests (design spec §V2.2 / §V2.8).

The app has one screen. Every retired page route is a deep link that renders
the canvas shell with the matching card pre-opened; the same route with
``HX-Request: true`` returns the panel body fragment only. Neither surface may
fall back to the old standalone page layouts.
"""

from __future__ import annotations

import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import Activity, OutreachMessage, Prospect, SavedView

# Retired route → the card that must be pre-opened on a plain GET.
CARD_ROUTES = [
    ("/", "status"),
    ("/status", "status"),
    ("/settings/profile", "profile"),
    ("/settings/providers", "providers"),
    ("/settings/usage", "usage"),
    ("/settings/general", "general"),
    ("/settings/import", "import"),
    ("/settings/saved-views", "saved_views"),
    ("/settings/retention", "retention"),
    ("/merge/duplicates", "merge"),
]


@pytest.fixture()
def db_client():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield db, TestClient(app)
    finally:
        app.dependency_overrides.clear()
        db.close()
        with suppress(OSError):
            os.unlink(path)


def _panel_title(html: str) -> str:
    match = re.search(r'<h2 class="panel-heading" data-panel-title>(.*?)</h2>', html, re.S)
    assert match, "panel body has no data-panel-title heading"
    return " ".join(match.group(1).split())


def _chip_href(html: str, label: str) -> str | None:
    """href of the removable filter chip with this visible label."""
    for match in re.finditer(r'<a class="filter-pill" href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        if "Remove filter:" in match.group(2) and label in match.group(2):
            return match.group(1)
    return None


@pytest.mark.parametrize(("url", "card"), CARD_ROUTES)
def test_retired_routes_open_the_right_card(db_client, url: str, card: str) -> None:
    """Plain GET → canvas shell + that card's panel, never a standalone page."""
    _, client = db_client
    r = client.get(url)
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text
    assert 'id="canvas-screen"' in r.text
    assert f'data-panel-open="{card}"' in r.text
    assert "data-panel-title" in r.text
    # The retired standalone chrome must not come back.
    assert 'class="page-bar"' not in r.text


@pytest.mark.parametrize(("url", "card"), CARD_ROUTES)
def test_same_routes_return_fragment_for_htmx(db_client, url: str, card: str) -> None:
    """HX-Request → panel body only (no document, no canvas chrome)."""
    _, client = db_client
    r = client.get(url, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<!DOCTYPE html>" not in r.text
    assert 'id="canvas-screen"' not in r.text
    assert "data-panel-title" in r.text


@pytest.mark.parametrize(("url", "card"), CARD_ROUTES)
def test_dual_render_parity(db_client, url: str, card: str) -> None:
    """The deep link and the HTMX panel render the same card body (one source)."""
    _, client = db_client
    plain = client.get(url)
    fragment = client.get(url, headers={"HX-Request": "true"})
    assert plain.status_code == fragment.status_code == 200
    assert _panel_title(plain.text) == _panel_title(fragment.text)


def test_prospects_hx_returns_board_fragment_only(db_client) -> None:
    _, client = db_client
    r = client.get("/prospects", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'data-zone="new"' in r.text
    assert "<!DOCTYPE html>" not in r.text
    assert "canvas-toolbar" not in r.text

    full = client.get("/prospects")
    assert full.status_code == 200
    assert 'id="canvas-screen"' in full.text
    assert 'data-zone="new"' in full.text


def test_prospect_card_keeps_stage_tag_in_the_name_row(db_client) -> None:
    """Stage is a top-right tag, not a footer pill (card layout contract).

    Keeps the corner tag on every board card and leaves the footer for the
    status dot, website badges and the card menu.
    """
    db, client = db_client
    prospect = Prospect(business_name="Corner Tag Hotel", stage="replied")
    db.add(prospect)
    db.commit()

    html = client.get("/prospects", headers={"HX-Request": "true"}).text
    card = re.search(r'<article class="prospect-card".*?</article>', html, re.S)
    assert card, "board fragment has no prospect card"
    card_html = card.group(0)

    name_row = re.search(r'<h3 class="pc-name-row">(.*?)</h3>', card_html, re.S)
    assert name_row, "card has no name row"
    assert "pc-stage-tag" in name_row.group(1)
    assert "data-stage-badge" in name_row.group(1)
    assert "replied" in name_row.group(1)

    foot = re.search(r'<div class="pc-foot">(.*?)</div>', card_html, re.S)
    assert foot, "card has no footer"
    assert "data-stage-badge" not in foot.group(1)


def test_message_page_does_not_render_the_message_orm_as_flash(db_client) -> None:
    """The message panel context uses `message` for the ORM row.

    The canvas shell's flash block must only render strings; a full-page
    message deep link used to print ``<app.models.OutreachMessage ...>``
    above the toolbar.
    """
    db, client = db_client
    prospect = Prospect(business_name="Flash Guard Hotel")
    db.add(prospect)
    db.commit()
    db.refresh(prospect)
    message = OutreachMessage(
        prospect_id=prospect.id, status="approved", active=True, current_text="Hello"
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    page = client.get(f"/messages/{message.id}/edit")
    assert page.status_code == 200
    assert "app.models.OutreachMessage object at" not in page.text
    assert "Back to prospect" in page.text


def test_outreach_route_frames_the_outreach_region(db_client) -> None:
    _, client = db_client
    r = client.get("/outreach")
    assert r.status_code == 200
    assert 'id="canvas-screen"' in r.text
    assert 'data-region="outreach"' in r.text

    fragment = client.get("/outreach", headers={"HX-Request": "true"})
    assert fragment.status_code == 200
    assert 'id="outreach-root"' in fragment.text
    assert 'data-region="outreach"' in fragment.text
    assert "<!DOCTYPE html>" not in fragment.text
    assert "canvas-toolbar" not in fragment.text


def test_rail_has_no_settings_tree(db_client) -> None:
    """Settings live on setup cards; the rail is Canvas + Shortcuts/Sign out."""
    _, client = db_client
    html = client.get("/").text
    aside = html.split('<aside class="sidenav"', 1)[1].split("</aside>", 1)[0]
    assert "/settings/" not in aside
    assert "/merge/" not in aside
    for label in ("Home", "Board", "Messages", "Status", "Shortcuts", "Sign out"):
        assert label in aside


def test_detail_routes_open_their_panels(db_client) -> None:
    db, client = db_client
    prospect = Prospect(business_name="Canvas Contract Hotel")
    db.add(prospect)
    db.commit()
    db.refresh(prospect)
    message = OutreachMessage(
        prospect_id=prospect.id, status="draft", active=True, current_text="Hello there"
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    cases = [
        ("/prospects/new", "prospect-new", "Add prospect"),
        (f"/prospects/{prospect.id}", f"prospect-{prospect.id}", "Canvas Contract Hotel"),
        (
            f"/prospects/{prospect.id}/edit",
            f"prospect-{prospect.id}-edit",
            "Canvas Contract Hotel",
        ),
        (f"/prospects/{prospect.id}/research", f"research-{prospect.id}", "Canvas Contract Hotel"),
        (f"/messages/{message.id}/edit", f"message-{message.id}", "Hello there"),
    ]
    for url, card, marker in cases:
        plain = client.get(url)
        assert plain.status_code == 200, url
        assert f'data-panel-open="{card}"' in plain.text, url
        assert marker in plain.text, url

        fragment = client.get(url, headers={"HX-Request": "true"})
        assert fragment.status_code == 200, url
        assert "<!DOCTYPE html>" not in fragment.text, url
        assert marker in fragment.text, url


def test_saved_view_picker_renders_apply_url(db_client) -> None:
    """Toolbar saved views (§V2.7 A3): picker lists the existing 303 endpoint."""
    db, client = db_client
    view = SavedView(name="Hotels in Berlin", filters={"category": "hotel", "q": "berlin"})
    db.add(view)
    db.commit()
    db.refresh(view)

    r = client.get("/prospects")
    assert r.status_code == 200
    assert f"/prospects/views/{view.id}/apply" in r.text
    assert "Hotels in Berlin" in r.text
    assert "Apply a view" in r.text


def test_active_filters_render_removable_chips(db_client) -> None:
    """Base spec §1.2: each active filter is a chip that drops only itself.

    Removal is plain URL state, so the chip URL must keep every other
    filter (order follows the toolbar: search, stage, tag, category...).
    """
    _, client = db_client
    r = client.get(
        "/prospects",
        params={"stage": "researched", "q": "berlin", "category": "hotel"},
    )
    assert r.status_code == 200
    assert 'class="filter-summary"' in r.text
    assert "Remove filter:" in r.text
    # Each chip drops only its own filter; the rest of the URL survives.
    assert _chip_href(r.text, "Search: berlin") == "/prospects?stage=researched&amp;category=hotel"
    assert _chip_href(r.text, "Stage: researched") == "/prospects?q=berlin&amp;category=hotel"
    assert _chip_href(r.text, "Category: hotel") == "/prospects?q=berlin&amp;stage=researched"
    # Stage lens clear keeps the other filters and is offered by name.
    assert "Show all zones" in r.text
    assert "Clear all" in r.text


def test_single_filter_has_no_clear_all(db_client) -> None:
    _, client = db_client
    r = client.get("/prospects", params={"stage": "new"})
    assert _chip_href(r.text, "Stage: new") == "/prospects"
    assert "Show all zones" in r.text
    assert "Clear all" not in r.text


def test_active_filters_absent_without_filters(db_client) -> None:
    _, client = db_client
    r = client.get("/prospects")
    assert 'class="filter-summary"' not in r.text
    assert "Remove filter:" not in r.text


def test_boolean_filter_chips_remove_cleanly(db_client) -> None:
    _, client = db_client
    r = client.get("/prospects", params={"include_archived": "true", "do_not_contact_only": "true"})
    # Dropping one boolean keeps the other as a truthy query param.
    assert _chip_href(r.text, "Archived included") == "/prospects?do_not_contact_only=true"
    assert _chip_href(r.text, "Suppressed only") == "/prospects?include_archived=true"


def test_flash_query_renders_banner(db_client) -> None:
    """POST redirects carry ?flash=…; the canvas must show the confirmation."""
    _, client = db_client
    r = client.get(
        "/prospects",
        params={"flash": "Message sent confirmed", "flash_type": "success"},
    )
    assert 'class="flash success"' in r.text
    assert "Message sent confirmed" in r.text

    # Unknown types fall back to info rather than becoming an arbitrary class.
    fallback = client.get("/prospects", params={"flash": "Saved", "flash_type": "nope"})
    assert 'class="flash info"' in fallback.text


def test_filters_panel_and_stage_lens(db_client) -> None:
    """Filters are a 360px panel (§V2.3); stage= renders a single-zone lens."""
    _, client = db_client

    page = client.get("/prospects", params={"stage": "researched", "q": "berlin"})
    assert page.status_code == 200
    assert 'data-panel-href="/prospects/filters?stage=researched&amp;q=berlin"' in page.text
    assert 'data-zone="researched"' in page.text
    # Phase 1.3: the full lane structure stays visible; the lens dims the rest.
    assert 'data-zone="new"' in page.text
    assert "board-lens" in page.text
    assert "lens-match" in page.text
    assert "Show all zones" in page.text

    fragment = client.get(
        "/prospects/filters",
        params={"stage": "researched", "q": "berlin"},
        headers={"HX-Request": "true"},
    )
    assert fragment.status_code == 200
    assert 'id="fp-stage"' in fragment.text
    assert 'id="fp-q"' in fragment.text
    assert 'value="berlin"' in fragment.text
    assert "Save this view" in fragment.text
    assert "<!DOCTYPE html>" not in fragment.text

    deep_link = client.get("/prospects/filters", params={"stage": "researched"})
    assert deep_link.status_code == 200
    assert 'data-panel-open="filters"' in deep_link.text


def test_prospect_form_labels_are_associated(db_client) -> None:
    """Axe `label`: every Add/Edit prospect input needs an explicit label."""
    _, client = db_client
    r = client.get("/prospects/new", headers={"HX-Request": "true"})
    assert r.status_code == 200
    for name in (
        "business_name",
        "category",
        "location_area",
        "location_city",
        "location_state",
        "location_country",
        "address",
        "phone_raw",
        "email",
        "website_url",
    ):
        assert f'for="pf-{name}"' in r.text
        assert f'id="pf-{name}"' in r.text


def test_panel_forms_post_to_their_own_action(db_client) -> None:
    """Panel forms must hx-post to their action, not the page URL.

    Regression: a valueless ``hx-post`` made HTMX POST to the current URL
    (e.g. /outreach → 405), so Approve/Confirm sent dead-ended from the canvas.
    """
    db, client = db_client
    prospect = Prospect(business_name="Panel Form Hotel")
    db.add(prospect)
    db.commit()
    db.refresh(prospect)
    message = OutreachMessage(
        prospect_id=prospect.id, status="pending_review", active=True, current_text="Hi there"
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    message_panel = client.get(f"/messages/{message.id}/edit", headers={"HX-Request": "true"})
    assert message_panel.status_code == 200
    assert f'hx-post="/messages/{message.id}/approve"' in message_panel.text
    assert "hx-post hx-target" not in message_panel.text

    opened_prospect = Prospect(business_name="Opened Message Hotel")
    db.add(opened_prospect)
    db.commit()
    db.refresh(opened_prospect)
    opened = OutreachMessage(
        prospect_id=opened_prospect.id,
        status="opened_in_whatsapp",
        active=True,
        current_text="Sent already",
    )
    db.add(opened)
    db.commit()
    db.refresh(opened)
    opened_panel = client.get(f"/messages/{opened.id}/edit", headers={"HX-Request": "true"})
    assert f'hx-post="/messages/{opened.id}/confirm-sent"' in opened_panel.text

    detail_panel = client.get(f"/prospects/{prospect.id}", headers={"HX-Request": "true"})
    assert detail_panel.status_code == 200
    assert f'hx-post="/prospects/{prospect.id}/stage"' in detail_panel.text


# ---------------------------------------------------------------------------
# Browser-QA regressions (manual pass 2026-11-17)
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]


def test_canvas_fit_keeps_the_readable_floor() -> None:
    """Spec §V2.1: first load fits the board **width** at ≥0.93, then pans.

    Regression: a spacing pass made ``fitBoardReadable`` fit both axes with
    only the global 0.5 floor, so any world taller than the viewport opened at
    50% (density-compact, card name under 12px) instead of the documented
    93–100% recorded in ``docs/QA_CHECKLIST.md``.
    """
    src = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")
    assert "var MIN_READABLE = 0.93" in src
    body = src.split("function fitBoardReadable(")[1].split("\n  }")[0]
    z_lines = [line for line in body.splitlines() if "var z =" in line]
    assert z_lines, "fitBoardReadable no longer computes a zoom level"
    assert "MIN_READABLE" in z_lines[0]
    assert "offsetHeight" not in z_lines[0], "height must not drive the zoom (pan instead)"


def test_panel_http_errors_keep_the_form() -> None:
    """An HTMX 4xx must not replace the panel with "Couldn't load this panel".

    Regression: ``htmx:responseError`` wiped the body for every failed request
    even though ``beforeSwap`` had already toasted the server's real reason
    ("A reason is required to clear suppression", "…ungrounded acknowledgement"),
    leaving a misleading connection error and destroying the form in progress.
    """
    src = (_ROOT / "app/static/js/panel.js").read_text(encoding="utf-8")
    handler = src.split('htmx:responseError"')[1].split("});")[0]
    assert "status >= 400" in handler, "HTTP errors must leave the panel alone"
    assert "panelBodyError(panel)" in handler, "transport failures still need the retry state"
    send_handler = src.split('htmx:sendError"')[1].split("});")[0]
    assert "panelBodyError(panel)" in send_handler


def test_flash_query_renders_exactly_one_notice(db_client) -> None:
    """A redirect flash must surface once on the canvas.

    It used to arrive three times: the shell banner plus two identical toasts
    (the route's ``message`` and ``?flash=`` are the same string).
    """
    _, client = db_client
    r = client.get("/prospects", params={"flash": "Duplicate flash check", "flash_type": "success"})
    assert r.status_code == 200
    assert r.text.count("Duplicate flash check") == 1
    assert 'class="flash success"' in r.text  # the canvas notice
    assert 'class="toasts"' not in r.text  # and not a second copy


def test_form_values_never_render_python_none() -> None:
    """A ``None`` field value renders as an empty input, never as "None".

    Regression: after a validation error every optional prospect field showed
    the literal text "None" (Jinja stringifies Python ``None``).
    """
    from starlette.requests import Request

    from app.routes.pages import templates

    # csrf_field is @pass_context and reads request cookies from the context.
    fake_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/prospects/new",
            "headers": [],
            "query_string": b"",
        }
    )
    fields = [
        "business_name",
        "category",
        "location_area",
        "location_city",
        "location_state",
        "location_country",
        "address",
        "phone_raw",
        "email",
        "website_url",
    ]
    html = templates.get_template("partials/panel_prospect_form.html").render(
        request=fake_request,
        prospect=None,
        action="/prospects/new",
        form={name: None for name in fields},
        field_errors={"business_name": "business name is required"},
        panel_mode=True,
        error=None,
    )
    assert 'value="None"' not in html
    assert 'value=""' in html
    # The banner names the field once — not "business name: business name is…" .
    assert "business name is required" in html
    assert "business name:</strong>" not in html


def test_validation_error_banner_does_not_repeat_the_field(db_client) -> None:
    """The create form's error banner shows each message once."""
    _, client = db_client
    client.get("/")
    r = client.post(
        "/prospects/new",
        data={
            "csrf_token": client.cookies.get("csrf_token", ""),
            "business_name": "   ",
            "category": "",
            "location_area": "",
            "location_city": "",
            "location_state": "",
            "location_country": "",
            "address": "",
            "phone_raw": "",
            "email": "",
            "website_url": "",
        },
    )
    assert r.status_code == 400
    assert "business name is required" in r.text
    assert "business name:</strong>" not in r.text
    assert 'value="None"' not in r.text


def test_missing_required_form_field_returns_readable_html(db_client) -> None:
    """A required ``Form(...)`` missing from the POST is a readable 400 page.

    Regression: FastAPI rejected it before the route could re-render, so the
    browser navigated away from the canvas onto a raw
    ``{"detail":[{"type":"missing"…}]}`` JSON document. JSON/API callers keep
    the 422 contract.
    """
    _, client = db_client
    client.get("/")
    r = client.post("/prospects/new", data={"csrf_token": client.cookies.get("csrf_token", "")})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("text/html")
    assert "That form could not be processed" in r.text
    assert "business name: Field required" in r.text  # readable, not a JSON blob
    assert '"detail"' not in r.text

    from tests.conftest import post_json

    api = post_json(client, "/api/v1/prospects", {})
    assert api.status_code == 422
    assert api.headers["content-type"].startswith("application/json")
    assert '"type"' in api.text


# ---------------------------------------------------------------------------
# Borderless / label / motion pass (owner requests 2026-11-17)
# ---------------------------------------------------------------------------


def test_detail_panel_drops_self_evident_section_labels(db_client) -> None:
    """Card labels that only restated their content are gone; control groups keep a heading.

    Owner decision on the label cleanup: "Drop the labels entirely — keep only
    headings that group many controls."
    """
    db, client = db_client
    prospect = Prospect(business_name="Label Sweep Hotel")
    db.add(prospect)
    db.commit()

    html = client.get(f"/prospects/{prospect.id}").text
    for gone in (
        "<h3>Key facts</h3>",
        "<h3>Research</h3>",
        "<h3>Opportunities</h3>",
        "<h3>Outreach</h3>",
        "<h3>Notes</h3>",
        "<h3>Activity</h3>",
        "<h3>Record reply</h3>",
        "<h3>Follow-ups</h3>",
    ):
        assert gone not in html, f"redundant label still rendered: {gone}"
    # The destructive group still names itself (it groups three control blocks).
    assert "<h3>Danger zone</h3>" in html


def test_panel_titles_render_as_headings(db_client) -> None:
    """Detail/message titles are h2 so the outline never skips (axe heading-order)."""
    db, client = db_client
    prospect = Prospect(business_name="Heading Order Ltd")
    db.add(prospect)
    db.commit()
    html = client.get(f"/prospects/{prospect.id}").text
    assert '<h2 class="prospect-name" data-panel-title>Heading Order Ltd</h2>' in html
    assert '<div class="prospect-name"' not in html


def test_activity_payload_renders_human_text(db_client) -> None:
    """Activity rows read as words, never as a JSON blob (UI pass).

    Regression: the panel printed `payload|tojson`, so the list showed
    ``{"reason": "asked to be removed"}`` next to the event name.
    """
    from app.services.activity import describe_payload

    db, client = db_client
    prospect = Prospect(business_name="Payload Hotel")
    db.add(prospect)
    db.commit()
    activity = Activity(
        prospect_id=prospect.id,
        type="do_not_contact_set",
        actor="user",
        payload={"reason": "asked to be removed"},
    )
    db.add(activity)
    db.commit()

    html = client.get(f"/prospects/{prospect.id}").text
    assert "reason: asked to be removed" in html
    assert "{&quot;reason&quot;" not in html and '{"reason"' not in html

    # formatter contract: nested maps flatten to readable pairs, empties drop out
    assert describe_payload({"run_id": 5, "status": "succeeded"}) == "run id: 5 · status: succeeded"
    assert (
        describe_payload({"steps": {"search": "ok", "inspect": "failed"}})
        == "steps: search ok, inspect failed"
    )
    assert describe_payload({"note": None, "flag": True}) == "flag: yes"
    assert describe_payload({}) == "" and describe_payload(None) == ""


def test_card_chips_use_colour_only_for_signals() -> None:
    """Prospect-card chips are neutral; colour survives only as a signal.

    Owner decision: "too many bright colors in all cards" → neutral tags,
    colour = signal (suppression, failed verification, check failed).
    """
    css = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
    neutral = css.split(".prospect-card .badge {", 1)[1].split("}", 1)[0]
    assert "background: var(--color-surface-raised)" in neutral
    assert "border-color: transparent" in neutral

    for signal in (
        ".prospect-card .badge.danger",
        ".prospect-card .badge.stage-do-not-contact",
        ".prospect-card .badge.stage-failed",
        ".prospect-card .badge.stage-check-failed",
    ):
        assert signal in css, f"signal chip lost its colour rule: {signal}"


def test_stage_tab_is_fused_to_the_card_edge() -> None:
    """The stage tag is a sunken corner tab that bends with the card, not a pill.

    Owner decision: "another design entirely for the tags that bends with the
    card" — negative margins pull it onto the card edge, its radius matches the
    card's, and it carries no outline.
    """
    css = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
    tab = css.split(".pc-name-row .pc-stage-tag {", 1)[1].split("}", 1)[0]
    assert (
        "margin: calc(-1 * (var(--card-pad) + 1px)) calc(-1 * (var(--card-pad) + 1px)) 0 0" in tab
    )
    assert "--card-pad: var(--space-3)" in css, "compact cards must override the inset"
    assert "border-radius: 0 var(--radius-md) 0 var(--radius-sm)" in tab
    assert "border: 0" in tab
    assert "background: var(--color-field)" in tab


def test_element_outlines_replaced_by_shade_levels() -> None:
    """No element keeps a box outline: levels come from the fill ladder.

    `--color-border` survives only on single-side dividers (rows, tabs,
    regions); a full `border: 1px solid var(--color-border)` box is a defect.
    """
    import re

    for name in ("app/static/css/app.css", "app/static/css/canvas.css"):
        css = (_ROOT / name).read_text(encoding="utf-8")
        boxes = [
            line.strip()
            for line in css.splitlines()
            if re.match(r"\s*border: 1px solid var\(--color-border\);", line)
        ]
        assert not boxes, f"{name} still draws box outlines: {boxes}"
    app = (_ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    assert "--color-field: #0e1014" in app, "shade ladder lost its control fill"


def test_canvas_motion_is_smooth_not_snappy() -> None:
    """Drawer-class motion decelerates over the slow token (owner: "make it smooth")."""
    app = (_ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    canvas = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
    assert "--dur-slow: 320ms" in app
    assert "--ease-out: cubic-bezier(0.22, 1, 0.36, 1)" in app
    assert "--dur-slow: 0ms" in app, "reduced-motion must zero the slow token too"
    panel = canvas.split(".panel {", 1)[1].split("}", 1)[0]
    assert "transition: transform var(--dur-slow) var(--ease-out)" in panel
    snap = canvas.split(".prospect-card.snapback {", 1)[1].split("}", 1)[0]
    assert "transform var(--dur-base) var(--ease-out)" in snap
    assert "120ms ease-out" not in snap


# ---------------------------------------------------------------------------
# Owner pass 2026-11-17b: em dashes, arrange, cards, nav, copy trims
# ---------------------------------------------------------------------------


def test_rendered_pages_carry_no_em_dashes(db_client) -> None:
    """Owner: "remove all em dashes between text" — checked on rendered HTML."""
    _, client = db_client
    for url in (
        "/prospects",
        "/settings/general",
        "/settings/providers",
        "/outreach",
        "/settings/import",
        "/settings/usage",
    ):
        html = client.get(url).text
        assert "—" not in html, f"{url} still renders an em dash"


def test_active_filter_band_sits_outside_the_toolbar(db_client) -> None:
    """The band is a sibling below the toolbar (it overlapped the toolbar row)."""
    _, client = db_client
    html = client.get("/prospects", params={"category": "hotel"}).text
    band = html.index('class="filter-summary"')
    toolbar_close = html.index("</div>", html.index("Add prospect"))
    assert band > toolbar_close, "filter band is still a flex child of the toolbar"


def test_arrange_control_and_layout_reset(db_client) -> None:
    """Owner: "a btn that properly arranges the elements when they are scattered"."""
    _, client = db_client
    html = client.get("/prospects").text
    assert 'data-canvas="tidy"' in html
    assert 'aria-label="Arrange the canvas layout"' in html
    js = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")
    assert "function tidy()" in js
    assert 'action === "tidy"' in js
    assert "b.zones = {}" in js  # scattered zones are forgotten, not re-applied
    # resize redraws wires; crossing the stacked/wide breakpoint re-arranges
    assert 'window.addEventListener("resize"' in js
    tail = js.split("lastLayoutMode", 1)[1]
    assert "scheduleWires()" in tail and "applyDefaultLayout()" in tail


def test_nav_marks_the_current_screen(db_client) -> None:
    """Owner: "active state is not working, only hover styling works"."""
    _, client = db_client
    board = client.get("/prospects").text
    assert 'class="nav-link active" data-canvas-view="world"' in board
    assert '<span class="nav-group-label">Session</span>' in board
    outreach = client.get("/outreach").text
    assert 'class="nav-link active" data-canvas-view="outreach"' in outreach


def test_cards_have_more_room() -> None:
    """Owner: "make the cards on canvas bigger with more spacing in and out"."""
    css = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
    js = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")
    assert "--zone-w: 288px" in css
    assert "--card-h: 84px" in css
    assert "--card-gap: var(--space-4)" in css
    assert "--zone-pad: 12px" in css
    assert "--card-pad: var(--space-4)" in css
    assert "var ZONE_W = 288" in js  # layout arithmetic mirrors the CSS token
    # hover states: fill + lift, never an underline (global a:hover loses)
    hover = css.split(".setup-card:hover {", 1)[1].split("}", 1)[0]
    assert "text-decoration: none" in hover and "translateY(-1px)" in hover
    assert ".canvas-screen a:hover" in css
    assert ".msg-card:hover" in css


def test_danger_zone_copy_is_not_repetitive(db_client) -> None:
    """Owner trims: no heading/button echoes, no empty-state filler."""
    db, client = db_client
    prospect = Prospect(business_name="Copy Sweep Clinic", stage="researched")
    db.add(prospect)
    db.commit()
    html = client.get(f"/prospects/{prospect.id}").text
    assert "Reason (optional)" in html  # unsuppressed: setting the flag
    assert "Change stage to" not in html  # the placeholder says it
    assert ">Move</button>" in html  # not "Change stage" again
    assert ">Archive</button>" in html  # not "Archive prospect"
    assert "No notes yet." not in html
    assert "No activity yet." not in html
    assert "One primary action at a time" not in html  # confirm dialog owns it

    # suppressed: the same block carries the required-reason label
    prospect.do_not_contact = True
    prospect.do_not_contact_reason = "asked to stop"
    db.commit()
    html = client.get(f"/prospects/{prospect.id}").text
    assert "Reason (required)" in html
    assert "asked to stop" in html
