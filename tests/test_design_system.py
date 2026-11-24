"""Regression tests for the token layer, .ds-* primitives and /dev/styleguide.

The incumbent UI keeps its exact computed values: tokens.css owns the literals,
app.css only aliases legacy names onto them. These tests pin that contract so a
future edit cannot silently restyle every screen or break standalone pages.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import _client

_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATES = _ROOT / "app/templates"
_TOKENS = (_ROOT / "app/static/css/tokens.css").read_text(encoding="utf-8")
_COMPONENTS = (_ROOT / "app/static/css/components.css").read_text(encoding="utf-8")
_APP = (_ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
_GUIDE = (_TEMPLATES / "dev/styleguide.html").read_text(encoding="utf-8")


def test_canonical_token_groups_are_complete() -> None:
    required = (
        "--surface-0",
        "--surface-1",
        "--surface-2",
        "--border",
        "--border-strong",
        "--text-primary",
        "--text-secondary",
        "--text-muted",
        "--accent",
        "--accent-hover",
        "--accent-subtle",
        "--success",
        "--success-subtle",
        "--warning",
        "--warning-subtle",
        "--danger",
        "--danger-subtle",
        "--info",
        "--info-subtle",
        "--radius-sm",
        "--radius-md",
        "--radius-lg",
        "--shadow-1",
        "--shadow-2",
        "--shadow-3",
    )
    for token in required:
        assert re.search(re.escape(token) + r":\s*[^;]+;", _TOKENS), token

    for step in (12, 13, 14, 16, 20, 24, 32):
        assert re.search(rf"--type-{step}:\s*[^;]+;", _TOKENS), step
        assert re.search(rf"--type-{step}-lh:\s*[^;]+;", _TOKENS), step
    for step in (1, 2, 3, 4, 5, 6, 7):
        assert re.search(rf"--space-{step}:\s*[^;]+;", _TOKENS), step
    assert "--font-mono" in _TOKENS and "--font-sans" in _TOKENS


def test_text_tokens_meet_aa_on_every_surface() -> None:
    """AA floor: 4.5:1 for normal text on each surface in the ladder."""

    def luminance(colour: str) -> float:
        channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def ratio(a: str, b: str) -> float:
        hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    def value(name: str) -> str:
        match = re.search(re.escape(name) + r":\s*(#[0-9a-fA-F]{6})", _TOKENS)
        assert match, name
        return match.group(1)

    surfaces = [
        value(n)
        for n in ("--surface-0", "--surface-1", "--surface-2", "--surface-panel", "--surface-field")
    ]
    for name in ("--text-primary", "--text-secondary", "--text-muted"):
        text = value(name)
        for surface in surfaces:
            assert ratio(text, surface) >= 4.5, (name, surface)


def test_app_css_only_aliases_tokens_without_self_reference() -> None:
    root = _APP.split(":root {", 1)[1].split("\n}", 1)[0]
    assert not re.search(r"(--[\w-]+):\s*var\(\1\)", root), "self-referential alias"
    assert "--color-bg: var(--surface-0)" in root
    assert "--fs-xs: var(--type-12)" in root
    assert "--font-sans" not in root, "canonical tokens belong in tokens.css"


def test_every_stylesheet_entry_point_loads_tokens_first() -> None:
    for path in _TEMPLATES.glob("*.html"):
        html = path.read_text(encoding="utf-8")
        if "/static/css/app.css" not in html:
            continue
        tokens = html.index("/static/css/tokens.css")
        app = html.index("/static/css/app.css")
        assert tokens < app, f"{path.name} must load tokens.css before app.css"


def test_components_expose_every_requested_primitive_and_state() -> None:
    groups = {
        "button": (
            ".ds-btn--primary",
            ".ds-btn--secondary",
            ".ds-btn--ghost",
            ".ds-btn--danger",
            ".ds-btn--sm",
            ":disabled",
        ),
        "form": (
            ".ds-label",
            ".ds-hint",
            ".ds-error",
            ".ds-input",
            ".ds-select",
            ".ds-textarea",
            '[aria-invalid="true"]',
        ),
        "card": (".ds-card__header", ".ds-card__actions"),
        "badge": tuple(
            f".ds-badge--{v}" for v in ("neutral", "success", "warning", "danger", "accent")
        ),
        "tabs": (".ds-tabs", ".ds-tab", '[aria-selected="true"]'),
        "drawer": (".ds-drawer", ".is-open", ".ds-drawer__scrim"),
        "empty": (
            ".ds-empty__icon",
            ".ds-empty__title",
            ".ds-empty__description",
            ".ds-empty__action",
        ),
        "table": (".ds-table", ".ds-table__empty"),
        "toast": (".ds-toast--success", ".ds-toast--warning", ".ds-toast--danger"),
        "page header": (
            ".ds-page-header__title",
            ".ds-page-header__subtitle",
            ".ds-page-header__actions",
        ),
        "link": (".ds-link", "var(--link)"),
    }
    for name, selectors in groups.items():
        for selector in selectors:
            assert selector in _COMPONENTS, f"{name}: {selector}"


def test_styleguide_is_unlinked_and_shows_every_state(no_token: None) -> None:
    client = _client()
    page = client.get("/dev/styleguide")
    assert page.status_code == 200
    assert "Design system" in page.text
    assert "/static/css/styleguide.css" in page.text

    for snippet in (
        "ds-btn--primary",
        "ds-btn--secondary",
        "ds-btn--ghost",
        "ds-btn--danger",
        "ds-btn--sm",
        "ds-label",
        "ds-hint",
        "ds-error",
        "ds-input",
        "ds-select",
        "ds-textarea",
        "ds-card__header",
        "ds-card__actions",
        "ds-badge--neutral",
        "ds-badge--success",
        "ds-badge--warning",
        "ds-badge--danger",
        "ds-badge--accent",
        "ds-tabs",
        "ds-tab",
        "ds-drawer",
        "ds-empty__icon",
        "ds-table",
        "ds-table__empty",
        "ds-toast",
        "ds-page-header",
        "ds-link",
        "ds-eyebrow",
    ):
        assert snippet in page.text, snippet

    # Hidden: no nav entry, no inline styles (hash-only CSP), no OpenAPI entry.
    assert "styleguide" not in (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "style=" not in _GUIDE
    paths = client.get("/api/openapi.json").json()["paths"]
    assert "/dev/styleguide" not in paths


# ===== Button consolidation: one implementation, one rule =====


def test_legacy_btn_is_grouped_into_the_single_button_implementation() -> None:
    """`.btn` renders through the .ds-btn rules; app.css keeps no second copy."""
    for grouped in (
        ".ds-btn,\n.btn {",
        ".ds-btn--primary,\n.btn.primary {",
        ".ds-btn--secondary,\n.btn.secondary {",
        ".ds-btn--ghost,\n.btn.ghost {",
        ".ds-btn--danger,\n.btn.danger,\n.btn.danger-outline {",
        ".ds-btn--sm,\n.btn.small {",
    ):
        assert grouped in _COMPONENTS, grouped
    # No standalone base/variant .btn rules left in app.css (context rules are fine).
    for standalone in ("\n.btn {", "\n.btn.primary {", "\n.btn.ghost {", "\n.btn.danger {"):
        assert standalone not in _APP, standalone


def test_secondary_is_raised_not_the_input_fill() -> None:
    """The black/grey/orange mix: secondary (the default) is surface-2, never
    --surface-field, so a button never reads as a text input."""
    base = _COMPONENTS.split(".ds-btn,\n.btn {", 1)[1].split("}", 1)[0]
    assert "background: var(--surface-2)" in base
    assert "--surface-field" not in base
    assert "--surface-2-hover:" in _TOKENS
    for selector in ("\n.mobile-menu-toggle {", "\n.nav-close {"):
        rule = _APP.split(selector, 1)[1].split("}", 1)[0]
        assert "background: var(--surface-2)" in rule, selector


def test_one_link_colour_and_orange_stays_an_action_signal() -> None:
    link_hover = _COMPONENTS.split(".ds-link:hover {", 1)[1].split("}", 1)[0]
    assert "color: var(--link)" in link_hover
    assert "accent" not in link_hover
    assert "color: var(--color-link)" in _APP.split("\na {", 1)[1].split("}", 1)[0]


def test_styleguide_shows_forced_states_and_legacy_equivalence(no_token: None) -> None:
    html = _client().get("/dev/styleguide").text
    for snippet in ("is-hover", "is-focus", 'class="btn primary"', 'class="btn ghost"'):
        assert snippet in html, snippet
    assert ".ds-btn.is-hover," in _COMPONENTS
    assert ".ds-btn.is-focus," in _COMPONENTS


def test_open_panel_demotes_the_page_level_primary() -> None:
    """One primary per view: the panel's action wins over the toolbar's."""
    canvas = (_ROOT / "app/static/css/canvas.css").read_text(encoding="utf-8")
    rule = canvas.split("body.panel-open .header-add.btn.primary {", 1)[1].split("}", 1)[0]
    assert "background: var(--surface-2)" in rule
    assert "body.panel-open .canvas-toolbar > .btn.primary," in canvas
