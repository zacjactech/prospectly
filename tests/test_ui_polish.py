"""Regression tests for the 2026-11-19 UI polish pass (design critique).

Each test pins one defect the critique raised, so the fix cannot quietly
revert: raw JSON reaching a browser, unconfirmed destructive actions, the
heading-order skip, the stage control buried in the Danger zone, Home's first
paint, and the setup-cluster disclosure.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Prospect
from app.services import users as users_service
from tests.conftest import _client, _setup

HTML = {"Accept": "text/html"}
JSON = {"Accept": "application/json"}


def _signed_in_owner(db: Session, token: str) -> TestClient:
    """Gate on, owner account created through /setup, session established."""
    client = _client()
    assert _setup(client).status_code == 303, "/setup must create the owner"
    return client


# ===== P1: raw JSON never reaches a browser =====


def test_unmatched_route_renders_the_error_page(db: Session, no_token: None) -> None:
    """A dead bookmark must not read as a crash.

    The router raises *Starlette's* HTTPException; the handler was registered
    only for FastAPI's subclass, so unmatched routes answered raw JSON.
    """
    client = _client()
    page = client.get("/does-not-exist", headers=HTML)
    assert page.status_code == 404
    assert "<h1>Not found</h1>" in page.text
    assert '"detail"' not in page.text

    # JSON callers keep the exact contract.
    api = client.get("/does-not-exist", headers=JSON)
    assert api.status_code == 404
    assert api.json() == {"detail": "Not Found"}


def test_csrf_failure_is_negotiated(db: Session, no_token: None) -> None:
    """A stale form token re-posted from the browser gets a page, not JSON."""
    client = _client()
    client.get("/")  # seeds the CSRF cookie
    payload = {"csrf_token": "not-the-cookie-value"}

    page = client.post("/settings/general", data=payload, headers=HTML)
    assert page.status_code == 403
    assert "<h1>Form expired</h1>" in page.text
    assert "Reload the page" in page.text

    api = client.post("/settings/general", data=payload, headers=JSON)
    assert api.status_code == 403
    assert api.json() == {"code": "csrf_failure", "message": "Missing or invalid CSRF token"}


# ===== P1: destructive actions confirm =====


def test_user_row_actions_confirm_before_acting(db: Session, token: str) -> None:
    """Disable ends a session immediately, so it must confirm like Delete."""
    client = _signed_in_owner(db, token)
    users_service.create_user(db, username="peer", password="peer-pass-99", role="member")
    db.commit()

    page = client.get("/settings/users", headers=HTML)
    assert page.status_code == 200

    for action, phrase in (
        ("toggle", "They are signed out immediately"),
        ("role", "role?"),
        ("delete", "prospects become shared"),
    ):
        match = re.search(
            rf'action="/settings/users/\d+/{action}"[^>]*data-confirm="([^"]*)"', page.text, re.S
        )
        assert match, f"{action} form has no data-confirm"
        assert phrase in match.group(1), f"{action} confirm copy: {match.group(1)}"


# ===== P2: heading order =====


def test_standalone_headings_stay_in_the_accessibility_tree(db: Session, no_token: None) -> None:
    """`display: none` dropped the only h2, so pages read H1 -> H3 -> H4."""
    css = _client().get("/static/css/app.css").text
    rule = css.split(".standalone-page .panel-heading", 1)[1].split("}", 1)[0]
    assert "display: none" not in rule, "the hidden h2 must not leave the a11y tree"
    assert "clip-path: inset(50%)" in rule, "should use the visually-hidden pattern"

    # The duplicated badge row and Edit button stay out of the tree so no
    # control is focusable twice.
    assert ".detail-page .detail-head .header-actions" in css


# ===== P2: stage control out of the Danger zone =====


def test_stage_control_sits_above_overview_not_in_danger_zone(db: Session, no_token: None) -> None:
    """The routine progression action read as destructive, buried in Danger."""
    prospect = Prospect(business_name="Stage Move Ltd", stage="new")
    db.add(prospect)
    db.commit()

    body = _client().get(f"/prospects/{prospect.id}", headers=HTML).text
    stage_i = body.index('id="detail-stage"')
    overview_i = body.index('id="detail-overview"')
    assert stage_i < overview_i, "stage control must sit above Overview"

    danger = body[body.index('<section class="card danger-zone">') :]
    assert "stage-form" not in danger, "stage form must leave the Danger zone"
    assert danger.count('<div class="danger-block">') == 2  # suppression + archive

    # Owner's 2026-11-18 "Stage override on one line" decision survives.
    assert '<div class="stage-override-row">' in body
    assert "<h4>Stage override</h4>" in body
    # ...and the outline has no skip now that the page h2 is visually hidden.
    assert '<h3 class="sr-only">Stage</h3>' in body


# ===== P1: Home first paint =====


def test_home_first_paint_hides_the_world_until_the_panel_closes(
    db: Session, no_token: None
) -> None:
    """The Status panel owns Home's first screen; the world is revealed on close."""
    client = _client()
    home = client.get("/", headers=HTML)
    assert home.status_code == 200
    assert '<body class="first-paint">' in home.text
    assert 'data-panel-open="status"' in home.text, "Home still opens the Status card"

    # Only Home: every other canvas route shows the world immediately.
    board = client.get("/prospects", headers=HTML)
    assert 'class="first-paint"' not in board.text
    assert "first_paint" not in board.text

    css = client.get("/static/css/app.css").text
    assert "body.first-paint .canvas-world" in css

    js = client.get("/static/js/app.js").text
    assert 'addEventListener("pl:panel-closed"' in js
    assert "first-paint" in js


# ===== P1: setup-cluster disclosure =====


def test_setup_cluster_discloses_behind_one_control(db: Session, no_token: None) -> None:
    """Nine cards at once was the canvas's largest decision point."""
    client = _client()
    body = client.get("/", headers=HTML).text

    cluster = body[body.index('<div class="setup-cluster"') :]
    cluster = cluster[: cluster.index("</div>", cluster.index("All setup"))]
    assert "data-collapsed" in cluster, "cluster starts collapsed"
    assert "data-setup-more" in cluster
    assert "All setup" in cluster
    assert 'aria-expanded="false"' in cluster

    # Cards stay in the DOM (deep links + tests) — only the grid/tab order drop.
    for card in (
        "status",
        "profile",
        "providers",
        "import",
        "saved_views",
        "retention",
        "merge",
        "usage",
        "general",
    ):
        assert f'data-setup-card="{card}"' in body, card

    css = client.get("/static/css/canvas.css").text
    assert '.setup-cluster[data-collapsed] > .setup-card:not([data-state="warn"])' in css

    js = client.get("/static/js/app.js").text
    assert "data-setup-more" in js


def test_warn_state_is_surfaced_on_every_card_that_can_warn(db: Session, no_token: None) -> None:
    """card_states already carried warn for profile/retention; the markup hid it."""
    body = _client().get("/", headers=HTML).text
    # Profile warns when no offer exists (fresh DB) — it must show the state.
    profile = body[body.index('data-setup-card="profile"') :]
    profile = profile[: profile.index("</a>")]
    assert 'data-state="warn"' in profile, "profile warn state must render"
    assert "sc-warn" in profile
