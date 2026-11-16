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

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import OutreachMessage, Prospect, SavedView

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
