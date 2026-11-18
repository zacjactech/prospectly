"""Canvas-only contract tests (design spec §V2.2 / §V2.8).

The app has one screen. Every retired page route is a deep link that renders
the canvas shell with the matching card pre-opened; the same route with
``HX-Request: true`` returns the panel body fragment only. Neither surface may
fall back to the old standalone page layouts.
"""

from __future__ import annotations

import datetime as dt
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
from app.models import Activity, Finding, OutreachMessage, Prospect, SavedView

# Retired views render as standalone pages now (owner 2026-11-18: "views
# without the canvas" become tab-based pages — no canvas behind them).
# Home is the deliberate exception: canvas with the Status card open.
RETIRED_ROUTES = [
    "/status",
    "/settings/profile",
    "/settings/providers",
    "/settings/usage",
    "/settings/general",
    "/settings/import",
    "/settings/saved-views",
    "/settings/retention",
    "/merge/duplicates",
]
RETIRED_CARDS = {
    "/status": "status",
    "/settings/profile": "profile",
    "/settings/providers": "providers",
    "/settings/usage": "usage",
    "/settings/general": "general",
    "/settings/import": "import",
    "/settings/saved-views": "saved_views",
    "/settings/retention": "retention",
    "/merge/duplicates": "merge",
}


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


@pytest.mark.parametrize("url", RETIRED_ROUTES)
def test_retired_routes_render_standalone_pages(db_client, url: str) -> None:
    """Plain GET → a real page: no canvas shell, no pre-opened panel.

    Owner 2026-11-18: these views become standalone tab-based pages; the
    canvas only backs the working surfaces (/ , /prospects, /outreach).
    """
    _, client = db_client
    r = client.get(url)
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text
    assert 'id="canvas-screen"' not in r.text
    assert "data-panel-open" not in r.text
    assert 'class="standalone-page' in r.text
    # The card body still renders its own heading (fragment parity source).
    assert "data-panel-title" in r.text
    assert 'class="page-bar"' not in r.text


def test_home_still_opens_status_on_the_canvas(db_client) -> None:
    """Home is the deliberate exception: canvas + Status card open."""
    _, client = db_client
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="canvas-screen"' in r.text
    assert 'data-panel-open="status"' in r.text


@pytest.mark.parametrize("url", RETIRED_ROUTES)
def test_same_routes_return_fragment_for_htmx(db_client, url: str) -> None:
    """HX-Request → panel body only (no document, no canvas chrome)."""
    _, client = db_client
    r = client.get(url, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<!DOCTYPE html>" not in r.text
    assert 'id="canvas-screen"' not in r.text
    assert "data-panel-title" in r.text


@pytest.mark.parametrize("url", RETIRED_ROUTES)
def test_dual_render_parity(db_client, url: str) -> None:
    """The deep link and the HTMX fragment render the same card body (one source)."""
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


def test_stage_tab_only_where_stages_share_a_frame(db_client) -> None:
    """Owner decision: the stage tab renders only in the Terminal frame.

    Three stages share one zone there, so only those cards need a tag. In
    their own lane the header already names the stage, and long stage names
    used to truncate inside a tab that repeated it.
    """
    db, client = db_client
    db.add(Prospect(business_name="Replied Only Ltd", stage="replied"))
    db.add(Prospect(business_name="Archived Only Ltd", stage="archived"))
    db.commit()

    html = client.get("/prospects", headers={"HX-Request": "true"}).text
    cards = re.findall(r'<article class="prospect-card".*?</article>', html, re.S)
    assert len(cards) == 2, f"expected 2 cards, got {len(cards)}"
    by_name = {("Archived Only Ltd" in c): c for c in cards}
    terminal = by_name[True]
    lane = by_name[False]

    # Terminal card carries the tab (name row only, never the footer)
    name_row = re.search(r'<h3 class="pc-name-row">(.*?)</h3>', terminal, re.S)
    assert name_row and "pc-stage-tag" in name_row.group(1)
    assert "data-stage-badge" in name_row.group(1)
    assert "archived" in name_row.group(1)

    # Lane card carries none: the zone header says "Replied"
    assert "pc-stage-tag" not in lane
    assert "data-stage-badge" not in lane


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

    # /prospects/new is an ACTION panel over the canvas and keeps its opener;
    # the view routes (detail, edit, research, message) are standalone pages.
    panel_cases = [("/prospects/new", "prospect-new", "Add prospect")]
    page_cases = [
        (f"/prospects/{prospect.id}", "Canvas Contract Hotel"),
        (f"/prospects/{prospect.id}/edit", "Canvas Contract Hotel"),
        (f"/prospects/{prospect.id}/research", "Canvas Contract Hotel"),
        (f"/messages/{message.id}/edit", "Hello there"),
    ]
    for url, card, marker in panel_cases:
        plain = client.get(url)
        assert plain.status_code == 200, url
        assert f'data-panel-open="{card}"' in plain.text, url
        assert marker in plain.text, url

    for url, marker in page_cases:
        plain = client.get(url)
        assert plain.status_code == 200, url
        assert 'id="canvas-screen"' not in plain.text, url
        assert "data-panel-open" not in plain.text, url
        assert 'class="standalone-page' in plain.text, url
        assert marker in plain.text, url

    fragment_markers = {c[0]: c[2] for c in panel_cases} | {c[0]: c[1] for c in page_cases}
    for url, marker in fragment_markers.items():
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


def test_flash_query_renders_a_popup_toast(db_client) -> None:
    """POST redirects carry ?flash=…; the canvas shows it as a popup toast.

    Owner 2026-11-17: "this should be a popup or tooltips and not green blend
    with the design" — no green banner is rendered on the canvas any more.
    """
    _, client = db_client
    r = client.get(
        "/prospects",
        params={"flash": "Message sent confirmed", "flash_type": "success"},
    )
    assert 'class="toast success"' in r.text
    assert "Message sent confirmed" in r.text
    assert 'class="flash success"' not in r.text

    # Unknown types fall back to info rather than becoming an arbitrary class.
    fallback = client.get("/prospects", params={"flash": "Saved", "flash_type": "nope"})
    assert 'class="toast info"' in fallback.text
    assert 'class="flash info"' not in fallback.text


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
    assert 'class="toast success"' in r.text  # the popup is the notice
    assert 'class="flash success"' not in r.text  # no banner copy


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
    # the section tab row is gone entirely (owner: "remove this Overview /
    # Research / Outreach / Activity" — sections are reached by scrolling)
    assert "detail-tabs" not in html
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
    assert '<h3 id="danger-zone">Danger zone</h3>' in html  # anchor for the undo link


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
    # exactly one current view, and it agrees with the URL (critique P1 #3)
    assert 'class="nav-link active" aria-current="true" data-canvas-view="board"' in board
    assert board.count('aria-current="true"') == 1
    assert '<span class="nav-group-label">Session</span>' in board
    outreach = client.get("/outreach").text
    assert 'class="nav-link active" aria-current="true" data-canvas-view="outreach"' in outreach
    assert outreach.count('aria-current="true"') == 1


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
    # owner: no "Do not contact" sub-heading, and the explanation sits ABOVE
    # the Block outreach button, not below the form
    assert "<h4>Do not contact</h4>" not in html
    assert html.index("Blocks drafts, approvals") < html.index(">Block outreach</button>")
    # owner: Archive button rides the head row beside the heading, not under it
    assert re.search(
        r'<div class="danger-block-head">\s*<h4>Archive</h4>(?:(?!</div>).)*?>Archive</button>',
        html,
        re.S,
    ), "Archive button is not on the head row"
    # owner: status badge sits on the Reason (optional) label row, not on its
    # own line above it
    assert (
        '<label for="dnc-reason">Reason (optional) '
        '<span class="badge muted">Not suppressed</span></label>'
    ) in html
    assert "Change stage to" not in html  # the placeholder says it
    assert ">Move</button>" in html  # not "Change stage" again
    assert ">Archive</button>" in html  # not "Archive prospect"
    assert "No notes yet." not in html
    assert "No activity yet." not in html
    assert "One primary action at a time" not in html  # confirm dialog owns it

    # suppressed: the required-reason label, the escape hatch next to the
    # banner, and no repeated "you can't" restatements (critique P2)
    prospect.do_not_contact = True
    prospect.do_not_contact_reason = "asked to stop"
    db.commit()
    html = client.get(f"/prospects/{prospect.id}").text
    assert "Reason (required)" in html
    assert "asked to stop" in html
    assert "Undo: Clear suppression" in html
    assert "Suppressed: no drafts" not in html
    assert "Suppressed: no outreach actions" not in html
    assert 'id="detail-outreach"' not in html  # card skipped while suppressed
    assert '<a href="#detail-outreach">Outreach</a>' not in html  # and no dangling tab


def test_region_swaps_and_density_flips_re_run_layout() -> None:
    """Owner: "cards are overlapping with other cards when it expand".

    Two coordinate invalidations must re-run the arrangement:
    - an HTMX swap can replace `#outreach-root` with server HTML that carries
      no inline styles, so the region falls back to 0,0 over the setup cluster;
    - crossing the density floor changes card heights, so rows measured at the
      old height would sit on top of each other.
    """
    js = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")
    handler = js.split('htmx:afterSwap"', 1)[1].split("});", 1)[0]
    assert 't.id === "outreach-root"' in handler and "relayout()" in handler
    assert 't.id === "board-root"' in handler and "init()" in handler

    relayout = js.split("function relayout()", 1)[1].split("function tidy()", 1)[0]
    assert "applyDefaultLayout()" in relayout and "scheduleWires()" in relayout
    assert "setView(" not in relayout and "fit(" not in relayout  # camera untouched

    density = js.split("function updateDensity()", 1)[1].split("function updateZoomReadout", 1)[0]
    assert "densityMode !== mode" in density and "relayout();" in density


def test_approval_gate_copy_and_ack_follow_grounding(db_client) -> None:
    """Critique P1 #4: one approval experience, honest copy, honest attestation.

    - both surfaces confirm with the two-step wording (the app never sends);
    - the acknowledgement appears only for ungrounded drafts, so a grounded
      draft can't train the operator to tick a risk box blindly.
    """
    db, client = db_client
    prospect = Prospect(business_name="Gate Sweep Studio", stage="outreach_ready")
    db.add(prospect)
    db.commit()
    db.refresh(prospect)
    message = OutreachMessage(
        prospect_id=prospect.id,
        status="pending_review",
        active=True,
        current_text="Hello, a quick idea for your studio.",
    )
    db.add(message)
    db.commit()

    copy = "Approve this draft? Opening WhatsApp is a separate step; you press Send."
    detail = client.get(f"/prospects/{prospect.id}").text
    assert copy in detail and "I accept ungrounded content" in detail
    editor = client.get(f"/messages/{message.id}/edit").text
    assert copy in editor and "I accept ungrounded content" in editor

    finding = Finding(
        prospect_id=prospect.id,
        kind="website",
        statement="No website found for this business",
        source_url="https://www.openstreetmap.org/",
        source_type="provider",
        retrieved_at=dt.datetime.now(dt.UTC),
        verification_status="verified",
        confidence="high",
        origin="provider",
        finding_key="no_website_gate_test",
    )
    db.add(finding)
    db.commit()
    message.grounding = [finding.id]
    db.commit()

    detail = client.get(f"/prospects/{prospect.id}").text
    assert "I accept ungrounded content" not in detail
    assert copy in detail
    editor = client.get(f"/messages/{message.id}/edit").text
    assert "I accept ungrounded content" not in editor


def test_toolbar_surfaces_the_approval_queue(db_client) -> None:
    """Critique P1 #2: the queue sits off the default fit, so the toolbar
    carries its count and pans to it."""
    db, client = db_client
    plain = client.get("/prospects").text
    # always rendered (client recounts it on every outreach swap) but hidden at 0
    chip = re.search(r"<button[^>]*data-pending-chip[^>]*>", plain)
    assert chip, "chip must always render"
    assert "hidden" in chip.group(0), "hidden while nothing is pending"

    prospect = Prospect(business_name="Queue Watch Ltd", stage="outreach_ready")
    db.add(prospect)
    db.commit()
    db.add(
        OutreachMessage(
            prospect_id=prospect.id,
            status="pending_review",
            active=True,
            current_text="Hello, small idea for your studio.",
        )
    )
    db.commit()

    html = client.get("/prospects").text
    chip = re.search(r"<button[^>]*data-pending-chip[^>]*>", html)
    assert chip and "hidden" not in chip.group(0), "visible while pending"
    assert 'class="pending-chip"' in html
    assert "Pending review · " in html
    assert '<span class="pending-count">1</span>' in html
    assert 'data-canvas-view="outreach"' in html
    # accessible name must contain the visible label verbatim
    # (Lighthouse: label-content-name-mismatch)
    m = re.search(r'class="pending-chip"[^>]*aria-label="([^"]+)"', html)
    assert m, "pending chip has no aria-label"
    assert "Pending review · 1" in m.group(1), m.group(1)


def test_card_drag_positions_in_world_space() -> None:
    """Owner: "when i drag a card the card is far away from the mouse dragging it".

    position() writes world coordinates (worldPoint strips pan/zoom), so the
    dragged card must be a child of the transformed world layer. Appended to
    the untransformed viewport it read those numbers as screen pixels and
    drifted from the cursor by the pan/zoom factor (measured: cursor +80,+60
    moved the card +2,+338 at load zoom and -210,+496 at 50%).

    snapBack runs with the card already re-inserted into a zone (inside the
    world), so its screen-space deltas must divide back through the zoom or
    the animation undershoots at zoom != 1.
    """
    js = (_ROOT / "app/static/js/board.js").read_text(encoding="utf-8")

    activate = js.split("function activate()", 1)[1].split("function position(", 1)[0]
    assert "worldLayer.appendChild(card)" in activate
    assert "viewport().appendChild(card)" not in activate
    assert "PlCanvas.world" in activate

    position = js.split("function position(", 1)[1].split("function move(", 1)[0]
    assert "worldPoint" in position  # coordinates stay world-space (spec §3.1)

    snap = js.split("function snapBack(", 1)[1].split("function startCardDrag", 1)[0]
    assert "zoom()" in snap and "/ z" in snap

    canvas = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")
    assert "world: function ()" in canvas  # the accessor board.js needs


def test_pending_chip_is_token_styled_and_live() -> None:
    """Critique P1 run 2: the chip rendered as a raw UA button (no rule in the
    repo), and P2: its count went stale because it lives outside both swap
    targets. Both are pinned here."""
    css = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
    chip = css.split(".pending-chip {", 1)[1].split("}", 1)[0]
    assert "var(--color-field)" in chip  # token fill, not UA gray
    assert "var(--radius-md)" in chip
    assert "font-family: var(--font-mono)" in chip
    assert "cursor: pointer" in chip
    dot = css.split(".pending-dot {", 1)[1].split("}", 1)[0]
    assert "width: 7px" in dot and "var(--color-warn)" in dot  # dot actually paints
    assert ".pending-chip[hidden]" in css  # author display beats UA [hidden]
    assert ".pending-chip:focus-visible" in css

    js = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")
    sync = js.split("function syncPendingChip()", 1)[1].split("function tidy()", 1)[0]
    assert '[data-msg-zone="pending_review"]' in sync
    assert ".msg-card" in sync
    assert "chip.hidden = n === 0" in sync
    after = js.split('t.id === "outreach-root"', 1)[1][:220]
    assert "syncPendingChip()" in after


def test_compact_density_never_hides_status_foot() -> None:
    """Critique P1 run 2: one zoom-out click (0.93/1.25=0.744 < floor) used to
    strip Suppressed / no website found / due text from every card — safety
    status (P5/P6) must survive every density. The meta row still hides (the
    density trade spec §2.5 keeps)."""
    css = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
    for m in re.finditer(r"\.canvas-screen\.density-compact[^{]*\{[^}]*\}", css):
        block = m.group(0)
        if ".pc-foot" in block:
            assert "display: none" not in block, f"foot hidden at compact: {block}"
    assert re.search(r"\.canvas-screen\.density-compact \.pc-meta \{\s*display: none;", css), (
        "meta row must still hide at compact"
    )


def test_world_edge_fades_mark_cuts() -> None:
    """Critique P1 run 2 (framing): the default view slices the pending card
    at the right edge and cuts the setup cluster above — an overlay marks every
    overflowing edge as "more this way" instead of a rendering bug.

    The fades are driven from the content union (the world shrink-wraps to
    0x0 — every child is absolutely positioned — so its offset box cannot be
    used for overflow math).
    """
    shell = (_ROOT / "app/templates/canvas/shell.html").read_text(encoding="utf-8")
    assert '<div class="world-edges" aria-hidden="true"></div>' in shell
    assert (
        shell.index("data-canvas-world")
        < shell.index("world-edges")
        < shell.index("canvas-controls")
    ), "overlay sits above the world, below the controls"

    css = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
    assert ".world-edges" in css and "pointer-events: none" in css.split(".world-edges {", 1)[1]
    for edge in "trbl":
        assert f".canvas-viewport.has-edge-{edge} .world-edges" in css

    js = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")
    fades = js.split("function updateEdgeFades()", 1)[1].split("var densityMode", 1)[0]
    assert ".canvas-zone, .setup-cluster, .region-label, .band-label, .msg-zone" in fades
    assert 'classList.toggle("has-edge-r"' in fades
    assert (
        "updateEdgeFades();"
        in js.split("function applyTransform()", 1)[1].split("function updateEdgeFades()", 1)[0]
    )


def test_help_table_documents_gestures_and_search() -> None:
    """Critique heuristic-10 gap: the gestures that define a canvas appeared
    nowhere in help, and `/` was documented without saying it opens Filters."""
    html = (_ROOT / "app/templates/base.html").read_text(encoding="utf-8")
    assert "Focus search (opens Filters)" in html
    assert "Wheel" in html and "Ctrl+wheel pans" in html
    assert "Drag background" in html and "drag a zone to rearrange" in html


def test_slash_shortcut_opens_filters_panel() -> None:
    """Critique P2 run 2: `/` was documented and dead on the canvas (no
    search input exists there). It now falls through to the Filters panel and
    focuses its search when the HTMX body lands."""
    js = (_ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    seg = js.split('if (e.key === "/")', 1)[1].split("// j/k", 1)[0]
    assert ".filter-open[data-panel-href]" in seg
    assert "#panel-root input[name='q']" in seg
    assert "focusFilterSearch" in seg and "setTimeout" in seg


def test_canvas_landmark_and_dead_wire_contract_removed() -> None:
    """Spec §6.3 promises a named canvas region; the run-2 review found the
    promise unmet and the wire contract dead (0 system-wire paths render,
    no JS reads the attributes)."""
    shell = (_ROOT / "app/templates/canvas/shell.html").read_text(encoding="utf-8")
    assert 'role="region" aria-label="Pipeline canvas"' in shell
    assert "data-system-wires" not in shell  # dead group, no JS reference
    assert "read-only wire layer" not in shell  # comment claimed setup connectors

    setup = (_ROOT / "app/templates/partials/canvas_setup_cluster.html").read_text(encoding="utf-8")
    assert "data-wire-target" not in setup  # 8 attrs, zero readers
    assert "wired to the region it feeds" not in setup


def test_arrange_confirms_and_nonform_confirms_are_wired() -> None:
    """Heuristic-3: Arrange silently discarded a hand-arranged canvas. The
    tidy button now carries data-confirm, and app.js honours data-confirm on
    controls OUTSIDE forms (capture phase, before the delegated canvas
    handler) while skipping in-form controls so nothing confirms twice."""
    shell = (_ROOT / "app/templates/canvas/shell.html").read_text(encoding="utf-8")
    m = re.search(r'<button[^>]*data-canvas="tidy"[^>]*>', shell, re.S)
    assert m, "tidy button missing"
    assert 'data-confirm="Arrange resets every manually placed zone' in m.group(0)

    js = (_ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    seg = js.split("Same confirm for controls", 1)[1].split("Whole-row navigation", 1)[0]
    assert 'closest("[data-confirm]")' in seg
    assert 'el.closest("form")' in seg  # in-form controls skip (no double confirm)
    assert "stopImmediatePropagation" in seg


def test_settings_page_is_tab_based(db_client) -> None:
    """Owner 2026-11-18: settings renders as one tab-based page — the strip is
    its navigation (these routes never had rail entries), and the deep-linked
    section is the active tab."""
    _, client = db_client
    r = client.get("/settings/providers")
    html = r.text
    assert r.status_code == 200
    assert 'id="canvas-screen"' not in html
    m = re.search(r'<nav class="tab-strip" aria-label="Settings sections">(.*?)</nav>', html, re.S)
    assert m, "settings tab strip missing"
    tabs = re.findall(r'<a href="([^"]+)"', m.group(1))
    assert tabs == [
        "/settings/general",
        "/settings/profile",
        "/settings/providers",
        "/settings/import",
        "/settings/saved-views",
        "/settings/retention",
        "/settings/usage",
        "/merge/duplicates",
    ]
    assert (
        '<a href="/settings/providers" class="active" aria-current="page">Providers</a>'
        in m.group(1)
    )
    assert m.group(1).count('aria-current="page"') == 1
    # h1 owns the page title; the card body renders beneath it
    assert "<h1>Settings</h1>" in html


def test_detail_page_is_tab_based(db_client) -> None:
    """Owner 2026-11-18: prospect detail is a standalone page with section
    tabs (no canvas behind), page head owns the name + badges + Edit action."""
    db, client = db_client
    prospect = Prospect(business_name="Tab Page Clinic", stage="replied")
    db.add(prospect)
    db.commit()
    html = client.get(f"/prospects/{prospect.id}").text
    assert 'id="canvas-screen"' not in html
    assert 'class="standalone-page detail-page"' in html
    assert "<h1>Tab Page Clinic</h1>" in html
    m = re.search(r'<nav class="tab-strip" aria-label="Prospect sections">(.*?)</nav>', html, re.S)
    assert m, "detail tab strip missing"
    assert m.group(1).count("<a ") == 4
    assert 'href="#detail-overview" class="active" aria-current="true"' in m.group(1)
    # the shared partial's own head stays in the DOM (fragment parity) but the
    # page CSS hides it so the name is not rendered twice
    assert '<h2 class="prospect-name" data-panel-title>Tab Page Clinic</h2>' in html


def test_canvas_links_navigate_instead_of_opening_panels() -> None:
    """Retired views became pages, so every link into them must be a plain
    link — only Filters and Add prospect may still open as canvas panels."""
    setup = (_ROOT / "app/templates/partials/canvas_setup_cluster.html").read_text(encoding="utf-8")
    assert "data-panel-href" not in setup

    region = (_ROOT / "app/templates/partials/canvas_outreach_region.html").read_text(
        encoding="utf-8"
    )
    assert 'class="msg-card-main" href="/messages/{{ m.id }}/edit">' in region
    assert "data-panel-href" not in region

    base = (_ROOT / "app/templates/base.html").read_text(encoding="utf-8")
    status_link = re.search(r'<a href="/status"[^>]*>', base).group(0)
    assert "data-panel-href" not in status_link

    shell = (_ROOT / "app/templates/canvas/shell.html").read_text(encoding="utf-8")
    panel_hrefs = set(re.findall(r'data-panel-href="([^"]+)"', shell))
    # the Filters button interpolates its href from the route context
    assert panel_hrefs <= {"/prospects/filters", "/prospects/new", "{{ filters_href }}"}

    board = (_ROOT / "app/static/js/board.js").read_text(encoding="utf-8")
    click = (
        board.split("Card click →", 1)[1]
        .split("document.addEventListener", 1)[1]
        .split("});", 1)[0]
    )
    assert 'window.location.href = "/prospects/"' in click
    assert "PlPanel.open" not in click

    appjs = (_ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    gs = appjs.split('target === "s"', 1)[1].split("}", 1)[0]
    assert 'window.location.href = "/status"' in gs
    assert "PlPanel.open" not in gs

    canvas = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")
    fallback = canvas.split("viewBtn && !vp", 1)[1].split("if (viewBtn && vp)", 1)[0]
    assert '"/prospects"' in fallback and '"/outreach"' in fallback


def test_detail_tab_strip_follows_the_hash() -> None:
    """The detail strip is in-page navigation: active state follows the hash
    (settings strip is server-rendered and needs no JS)."""
    js = (_ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    seg = js.split("function syncDetailTabs()", 1)[1].split("Chip previews", 1)[0]
    assert "location.hash" in seg
    assert 'classList.toggle("active", on)' in seg
    assert "hashchange" in js


def test_phone_layout_system() -> None:
    """Owner 2026-11-18 mobile audit: work first, one accent action in the
    header, two-row toolbar, 44px touch floor, labeled stacked tables."""
    base = (_ROOT / "app/templates/base.html").read_text(encoding="utf-8")
    assert 'class="btn primary header-add"' in base
    assert 'data-panel-href="/prospects/new"' in base  # header action opens the add panel

    appcss = (_ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    touch = appcss.split("@media (pointer: coarse), (max-width: 767px)", 1)[1]
    for sel in (".btn", ".pending-chip", ".nav-link", ".tab-strip a", "select", "textarea"):
        assert sel in touch, f"{sel} missing from the touch floor"
    assert "min-height: 44px" in touch
    assert ".header-add" in touch and "margin-left: auto" in touch
    assert ".app-header-title" in touch  # title deduped on phone (h1 owns it)

    css = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
    toolbar = css.split("/* <768: the toolbar drops Add prospect", 1)[1].split("/* <1024:", 1)[0]
    assert "display: none" in toolbar  # toolbar accent action hidden on phone
    stacked = css.split("@media (max-width: 1023px)", 1)[1]
    assert ".canvas-zone + .canvas-zone" in stacked and "margin-top: var(--space-3)" in stacked
    assert "padding: var(--space-4)" in stacked  # 16px gutters


def test_work_first_order_on_small_screens() -> None:
    """Owner: setup's nine cards sat above the work (first zone at y=813 of
    844). Stacked mode moves setup after the pipeline + messages — DOM order
    follows the visual order so tab/SR match what you see; wide restores the
    canonical order. Idempotent across re-inits and mode crossings."""
    js = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")
    fn = js.split("function applyStackOrder()", 1)[1].split("function init()", 1)[0]
    assert "world.querySelector('[data-region-label=\"setup\"]')" in fn
    assert "world.appendChild(setupLabel)" in fn
    assert "world.appendChild(setup)" in fn
    assert "insertBefore(setupLabel, boardLabel)" in fn  # wide restore
    assert "window.innerWidth < 1024" in fn
    # called on init (covers HTMX re-inits) and on layout-mode crossings
    init = js.split("function init()", 1)[1].split("function tidy()", 1)[0]
    assert "applyStackOrder();" in init.split("applyDefaultLayout()")[0]
    resize = (
        js.split("var lastLayoutMode", 1)[1].split("window.addEventListener", 1)[0]
        + js.split('window.addEventListener("resize"', 1)[1].split("});", 1)[0]
    )
    assert "applyStackOrder();" in resize


def test_panel_tables_carry_stacked_labels() -> None:
    """The responsive table transform stacks cells with `td::before` from
    data-label; without the attrs headers vanish and values float unlabeled."""
    cards = _ROOT / "app/templates/canvas/cards"
    expectations = {
        "general.html": ["Provider", "Used today", "Configured"],
        "usage.html": ["Provider", "Kind", "Period", "Rate limited", "Prompt tokens"],
        "merge.html": ["Merge", "Source", "Target", "Score", "Reason"],
        "import.html": ["Business name", "Category", "City", "Status", "Metric", "Count"],
        "retention.html": ["Prospect", "Stage", "Last activity", "Actions"],
    }
    for name, labels in expectations.items():
        html = (cards / name).read_text(encoding="utf-8")
        for label in labels:
            assert f'data-label="{label}"' in html, f"{name}: missing {label}"
