"""Regressions for the dark-UI polish pass: docs under CSP, warnings, focus,
type floors, and the board's Pending review card. Each test pins one defect so
it cannot quietly return.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Prospect
from tests.conftest import _client

_ROOT = Path(__file__).resolve().parents[1]
_APP_CSS = (_ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
_TOKENS_CSS = (_ROOT / "app/static/css/tokens.css").read_text(encoding="utf-8")
_CANVAS_CSS = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
_CANVAS_JS = (_ROOT / "app/static/js/canvas.js").read_text(encoding="utf-8")


def _css_rule(css: str, selector: str) -> str:
    """First declaration block for a selector (enough for these flat rules)."""
    return css.split(selector + " {", 1)[1].split("}", 1)[0]


# ===== P1: API docs render from local assets under a route-scoped CSP =====


def test_api_docs_are_self_hosted_and_csp_scoped(no_token: None) -> None:
    client = _client()

    for path in ("/api/docs", "/api/redoc"):
        page = client.get(path)
        assert page.status_code == 200
        assert "/static/vendor/docs/" in page.text
        assert "cdn.jsdelivr.net" not in page.text
        assert "cdn.redoc.ly" not in page.text
        assert "<script>" not in page.text, "no inline bootstrap under script-src 'self'"
        csp = page.headers["content-security-policy"]
        assert "style-src 'self' 'unsafe-inline'" in csp
        assert "worker-src blob:" in csp
        assert "unsafe-inline" not in csp.split("style-src")[0]

    # The rest of the app keeps the strict hash-only policy.
    app_csp = client.get("/").headers["content-security-policy"]
    assert "'sha256-faU7yAF8NxuMTNEwVmBz+VcYeIoBQ2EMHW3WaVxCvnk='" in app_csp
    assert "unsafe-inline" not in app_csp
    assert "worker-src" not in app_csp


def test_vendored_redoc_uses_the_local_logo() -> None:
    bundle = (_ROOT / "app/static/vendor/docs/redoc.standalone.js").read_text(encoding="utf-8")
    assert "https://cdn.redoc.ly/redoc/logo-mini.svg" not in bundle
    assert "/static/vendor/docs/redoc-logo.svg" in bundle
    assert (_ROOT / "app/static/vendor/docs/redoc-logo.svg").is_file()


# ===== P2: mismatch banner is one warning, not two grey sentences =====


def test_detail_mismatch_banner_is_one_warning(db: Session, no_token: None) -> None:
    prospect = Prospect(business_name="Mismatch Ltd", stage="opportunity_identified")
    db.add(prospect)
    db.commit()

    html = _client().get(f"/prospects/{prospect.id}").text
    match = re.search(r'<aside class="banner warn"[^>]*>(.*?)</aside>', html, re.S)
    assert match, "the mismatch warning must be a warning banner"
    banner = match.group(0)
    text = " ".join(re.sub(r"<[^>]+>", "", match.group(1)).split())
    assert text == ("Stage doesn’t match the saved data. Run research or correct the stage below.")
    assert "no research findings yet" not in text
    assert "no opportunities recorded" not in text
    assert html.count('class="banner warn"') == 1
    assert 'role="status"' in banner
    assert "banner-icon" in banner

    warn = _css_rule(_APP_CSS, ".banner.warn")
    assert "background: var(--color-warn-light)" in warn
    assert "border-left: 3px solid var(--color-warn)" in warn


# ===== P2: focus is a 2px keyboard ring; autofocus stays quiet =====


def test_focus_ring_is_keyboard_only_without_a_glow() -> None:
    assert ":where(a, button, input, select, textarea, [tabindex]):focus-visible" in _APP_CSS
    ring = _css_rule(
        _APP_CSS, ":where(a, button, input, select, textarea, [tabindex]):focus-visible"
    )
    assert "outline: 2px solid var(--color-accent)" in ring
    assert "box-shadow: none" in ring
    assert "0 0 0 3px var(--focus-ring)" not in _APP_CSS
    assert "--focus-ring" not in _APP_CSS
    assert ".autofocused:focus," in _APP_CSS
    assert "input:focus," not in _APP_CSS
    assert "prospect-card:focus-visible" not in _CANVAS_CSS or (
        "box-shadow: var(--glow-accent)"
        not in _css_rule(_CANVAS_CSS, ".prospect-card:focus-visible")
    )

    auth_js = (_ROOT / "app/static/js/auth.js").read_text(encoding="utf-8")
    assert "input[autofocus]" in auth_js
    assert 'classList.add("autofocused")' in auth_js
    assert 'classList.remove("autofocused")' in auth_js


# ===== P2: readable type and AA muted contrast =====


def test_type_floors_and_muted_contrast_tokens() -> None:
    for css in (_APP_CSS, _CANVAS_CSS):
        assert not re.search(r"font-size:\s*(9|10|11)px", css)
        assert "clamp(11px" not in css
        assert "clamp(10px" not in css
    # Canonical values live in tokens.css; app.css only aliases them.
    assert "--type-12: 0.75rem" in _TOKENS_CSS  # 12px meta
    assert "--type-13: 0.8125rem" in _TOKENS_CSS  # 13px body/labels
    assert "--text-muted: #9ba0a8" in _TOKENS_CSS  # ≥4.5:1 on every shade
    assert "--fs-xs: var(--type-12)" in _APP_CSS
    assert "--fs-sm: var(--type-13)" in _APP_CSS
    assert "--color-text-subtle: var(--text-muted)" in _APP_CSS


# ===== P1: the Pending review card reaches the board's right edge =====


def test_board_fit_frames_the_pending_review_zone(no_token: None) -> None:
    assert _client().get("/static/js/canvas.js").text == _CANVAS_JS

    stage = _CANVAS_JS.split("function stageBounds()", 1)[1].split("function fitBoard", 1)[0]
    assert 'data-msg-zone="pending_review"' in stage
    assert "out.offsetLeft + outPending.offsetLeft" in stage

    fit = _CANVAS_JS.split("function fitBoardReadable", 1)[1].split("function focusCard", 1)[0]
    assert "var pad = 8;" in fit
    assert "stageRight" in fit
    assert "rect.width - pad" in fit

    outreach = _css_rule(_CANVAS_CSS, ".outreach-region")
    assert "--outreach-zone-w: 232px" in outreach
    assert "gap: var(--space-2)" in outreach
    assert "var OUTREACH_GAP_X = 8" in _CANVAS_JS
    assert "var ZONE_W = 288" in _CANVAS_JS, "board zones keep their readable width"


def test_saved_board_view_is_never_restored_stale() -> None:
    """A fit/old/other-width saved view must re-fit, or the Pending review
    card stays clipped for anyone with an existing pl.canvas.v1 blob."""
    assert "b.vpW = Math.round(vp.clientWidth)" in _CANVAS_JS
    assert "b.fit = viewIsFit" in _CANVAS_JS
    init = _CANVAS_JS.split("var lensStage", 1)[1].split("vp.addEventListener", 1)[0]
    assert "!b.fit &&" in init
    assert "b.vpW === Math.round(vp.clientWidth)" in init
    fit = _CANVAS_JS.split("function fitBoardReadable", 1)[1].split("function focusCard", 1)[0]
    assert "viewIsFit = true" in fit
    resize = _CANVAS_JS.split('window.addEventListener("resize"', 1)[1]
    assert 'mode === "wide" && viewIsFit' in resize
    assert "fitBoardReadable(fitCap)" in resize


def test_page_load_panel_focus_is_quiet() -> None:
    panel_js = (_ROOT / "app/static/js/panel.js").read_text(encoding="utf-8")
    adopt = panel_js.rsplit("adopted: true", 1)[1]
    assert 'closeBtn.classList.add("autofocused")' in adopt
    assert 'closeBtn.classList.remove("autofocused")' in adopt


def test_small_button_and_error_text_meet_the_13px_floor() -> None:
    components = (_ROOT / "app/static/css/components.css").read_text(encoding="utf-8")
    # .ds-btn--sm is grouped with the legacy .btn.small (last selector in the list).
    assert ".ds-btn--sm,\n.btn.small {" in components
    for selector in (".btn.small", ".ds-error"):
        rule = _css_rule(components, selector)
        assert "font-size: var(--type-13)" in rule, selector
