# Pipeline Canvas V2 — implementation task tracker

**Created:** 2026-11-16
**Source of truth:** `docs/design/pipeline-canvas.md` §V2 (owner-approved
2026-11-12). This file tracks *execution*; it does not change the contract.
**Gate:** `uv run pytest` green at the §V2.7 baseline (331 tests), plus
`ruff check`, `ruff format --check`, `mypy app` clean. A task is only checked
when its stated proof exists in the repo or in `docs/QA_CHECKLIST.md`.

**Snapshot at creation:** 327 passed / 4 failed. Lint, format and types clean.
The four red tests assert the retired page layouts; one of them (saved views)
also exposes a genuinely missing A3 feature.

**Snapshot after A/B:** 374 passed / 0 failed (331 baseline + 43 canvas
contract tests). Lint/format/types clean. The 5,000-prospect budget surfaced a
real regression in the setup cluster (see A4) — fixed in the same pass.

Status legend: `[ ]` open · `[~]` in progress · `[x]` done · `[!]` blocked.

---

## A — Restore the test gate

- [x] **A1. Migrate the four stale UI assertions (§V2.2 test-impact rule).**
  - `test_dashboard_renders_pipeline_strip_without_fake_metrics` → assert the
    Status card contract: pipeline strip is gone, "Needs attention" and real
    metrics are present, prospect names may appear on the board.
  - `test_html_wizard_form_replay_end_to_end` / CSRF multipart test → assert
    the `01 Upload → 02 Preview → 03 Commit` mini-pipeline. The upload result
    marker is `Detected columns:`, not `Step 2`.
  - `test_list_page_offers_save_view_when_filters_active` → kept as a real
    requirement; see B1 (the form was missing from the canvas toolbar).
  - Proof: `tests/test_dashboard.py`, `tests/test_csrf.py`,
    `tests/test_csv_import.py`, `tests/test_discovery_and_views.py`.

- [x] **A2. Canvas contract tests (§V2.2 / §V2.8).** New
  `tests/test_canvas_contract.py`:
  - every retired route on a plain GET returns the canvas shell with the
    correct card pre-opened (`data-panel-open="…"`), never a standalone page
    (**amended 2026-11-18**: retired views became standalone tab pages — see
    §V2.2 in pipeline-canvas.md; Home and the action panels keep this rule);
  - the same routes with `HX-Request: true` return the panel fragment only
    (no `<html>`, no canvas chrome);
  - a representative card body is byte-identical in both renderings
    (dual-render parity);
  - `/prospects` with `HX-Request` returns the board fragment only;
  - the rail contains no settings links (cards own settings);
  - `/outreach` renders the canvas with the outreach region.

- [x] **A3. Cover the restored saved-views toolbar.** Seed a saved view, GET
  `/prospects`, assert its apply URL is rendered; filtered GET still renders
  the "Save this view" POST form.

- [x] **A4. Keep the 5,000-prospect render budget (Phase 5 exit criterion).**
  The new setup cluster called `find_fuzzy_duplicates()` on every canvas
  render, scanning all live prospects (~99 ms of a 113 ms response at 5,000
  rows). The Merge card now computes pairs when opened; the card meta line is
  a static advertisement. Proof: `test_phase5_performance.py` green,
  `docs/design/canvas-implementation-tasks.md` execution log.

---

## B — Complete A3 (outreach region + toolbar polish)

- [x] **B1. "Save this view" form on the canvas toolbar when filters are
  active** (was present on the retired list page; missing after the V2 port).
  - Hidden structured filters + CSRF + required name → POST
    `/settings/saved-views`.
  - Proof: `test_list_page_offers_save_view_when_filters_active`.

- [x] **B2. Saved-views picker on the toolbar** (native `<select>`, applies a
  saved view through the existing 303 endpoint; "Manage" opens the card).
  - JS navigation lives in an external script (CSP: no inline handlers).
  - Proof: `test_canvas_contract.py::test_saved_view_picker_renders_apply_url`.

- [x] **B3. Filter summary chips / stage lens / Filters panel (owner decision:
  build them).** Active filters render as removable chips under the toolbar.
  Each chip is a plain link to `/prospects` with every *other* filter
  preserved — URL state only, so removal works without JS and cannot drift
  from the board. The stage chip doubles as the lens clear: `?stage=` renders
  a **single matching zone** and a named **Show all zones** link is offered;
  **Clear all** appears with 2+ filters. Filters and saved views live in the
  contracted **360px Filters panel** (`/prospects/filters`), leaving a 49px
  toolbar. Chips use the spec's accent-light/accent-border treatment and wrap
  on mobile. Proof: `tests/test_canvas_contract.py` (removable URLs, boolean
  preservation, stage lens, absent when unfiltered, panel/deep link).

- [x] **B4. Outreach region interaction pass.** Message card → panel →
  approval chain exercised in a real browser against seeded data: pending →
  approve → approved zone; approved → panel offers only **Open in WhatsApp**
  (P1); opened → **Confirm sent** records the send and leaves the region.
  Two blockers found and fixed:
  - **Pan captured link clicks.** `canvas.js` started a pan for pointerdown
    outside `.canvas-zone`, retargeting the trailing click; setup cards and
    message cards never opened their panels. Interactive elements are now
    excluded from pan (`app/static/js/canvas.js`).
  - **Panel forms posted to the page URL.** A valueless `hx-post` made HTMX
    POST to `/outreach` (405) / `/prospects`; every panel form now carries
    its own action URL, and board/outreach regions refresh from their own HX
    fragments (`/prospects` board, `/outreach` region). Proof:
    `test_panel_forms_post_to_their_own_action`,
    `test_outreach_route_frames_the_outreach_region`.
  - **Redirect confirmations were invisible.** POST → 303 flash parameters
    were never read by the canvas; `_render_canvas` now surfaces a
    whitelisted `?flash=…` banner. Proof: `test_flash_query_renders_banner`.

---

## C — Documentation sync (same change, AGENTS.md §9)

- [x] **C1. `docs/design/pipeline-canvas.md` status table** — record A1–A3
  implementation, test gate, browser validation and review state honestly.
- [x] **C2. `docs/QA_CHECKLIST.md`** — add a Pipeline Canvas section with the
  canvas contract tests and a dated browser pass (replaces the stale
  "330 tests" line).
- [x] **C3. `README.md` + `docs/IMPLEMENTATION_ROADMAP.md`** — canvas is a
  post-`v0.1.0` UI milestone. Add it explicitly instead of silently changing
  what "Phase 5 complete" means.
- [x] **C4. `docs/API_DESIGN.md` / `SYSTEM_ARCHITECTURE.md` /
  `UI_UX_SPECIFICATION.md`** — already updated in the canvas port; re-check
  against the final code before commit.

---

## D — Browser / visual validation (§V2.8, design spec §8.3)

- [x] **D1. Screenshots at 375 / 430 / 768 / 1280 / 1440.** Captured
  2026-11-16 with Playwright viewport emulation (see D4 for paths). Final
  responsive behavior: single 49px toolbar row at 1024+ (saved view hidden
  below 1280, available in Filters); board fit 93–100% on load (Home fits the
  whole world at 60%); 360px Filters panel; stage lens hides setup/outreach/
  wires and fits the single zone at 160%; stacked flow below **1024px** with
  a 2-column setup cluster (board at 657px/547px), **119px phone toolbar**,
  full card meta on mobile, **68px cards with 12px gaps** and 16px/12px setup
  gaps; density stays full at board fit (floor 0.75). Wire annotations and
  setup “connects to” lines are removed (wires only). Verified no
  overflow/console errors at every width.
- [x] **D2. A11y + contrast evidence.** 2026-11-16 real-browser pass:
  - **axe-core 4.10** (WCAG 2.0/2.1 A+AA): **0 violations** at 1440 (canvas,
    chips) and 375 (filters collapsed, then drawer + Add-prospect panel open);
    `/outreach` message panel also 0. Earlier `label` violations in the
    prospect form fixed with explicit `for`/`id` pairs
    (`test_prospect_form_labels_are_associated`).
  - **Lighthouse:** accessibility 1.0, best-practices 1.0 (SEO 0.75 only
    missing meta description); `label-content-name-mismatch`, `link-name`,
    `select-name`, `target-size` cleared.
  - **Computed contrast:** heading/muted 7.7:1, subtle 6.03:1, filter chip
    5.47:1, primary action 6.76:1, zone title 7.7:1 — all ≥ AA.
  - **Interaction:** focus trap wraps, Escape closes + restores invoker focus,
    backdrop closes, dirty-form confirm keeps the panel on cancel, keyboard
    map (`?`, `/`, `j`/`k`, `Enter`, `m`, `g h/p/o/s`, zoom keys) verified,
    reduced-motion media query drops transitions to 0.01ms.
  - Evidence is recorded here; raw Lighthouse/axe output stays local.
- [x] **D3. Independent visual review** at the `design-reviewer` gate —
  **PASS, 91/100** (2026-11-16, no blockers) after the panel/lens/toolbar/
  label fixes. Residual non-blocking notes: default desktop fit is 54–62%
  (small setup-card state text), and the nine-card mobile setup list could
  compress further.
- [x] **D4. Evidence hygiene.** `.ui-qa/` is intentionally gitignored; the
  pass summary + file list lives in `QA_CHECKLIST.md`, raw images stay local.

---

## E — Commit / release

- [x] **E1. Scoped commits.** Test/contract + perf fix; canvas UI/assets +
  toolbar/chips/responsive fixes; docs/design/tracker (final tree green).
- [x] **E2. Decide versioning.** Whole-app UI replacement → **`0.2.0`**
  (`pyproject.toml`, `app/config.py`, `docs/openapi.json`, README status).
- [x] **E3. Final gate** on the committed tree: tests green, lint/format/types
  clean, canvas startup smoke.

---

## Execution log

| Date | Task | Result |
|---|---|---|
| 2026-11-16 | A1 | Four assertions migrated to the canvas contract; saved-view test retained pending B1. |
| 2026-11-16 | A2/A3 | `tests/test_canvas_contract.py` added; covers deep links, HX fragments, dual render, rail, board fragment, saved-view picker. |
| 2026-11-16 | B1/B2 | Toolbar restored: save-current-view form + saved-views picker (external JS, CSP-safe). |
| 2026-11-16 | A4 | Removed the per-render fuzzy-duplicate scan from the setup cluster; 5,000-prospect render back under budget. |
| 2026-11-16 | C1/C2 | Design status table and QA checklist canvas section updated. |
| 2026-11-16 | C3/C4 | README status + docs map and roadmap post-release milestone updated; contracts re-checked. |
| 2026-11-16 | D1/D2 (partial) | Desktop browser smoke on port 8765: toolbar/picker/save form, panel open/close, picker nav, no console errors. Lighthouse: a11y 1.0, best-practices 1.0 (zoom label/rail/select names fixed, `?v=2` cache-busted). Mobile 375/430/768 screenshots + full a11y/contrast evidence still open. |
| 2026-11-16 | B3 | Removable filter chips built (URL-only links, all other filters preserved), stage lens as **Show all zones**, accent styling, wrap on mobile; 4 contract tests added. |
| 2026-11-16 | D1 | Responsive pass: 1024–1439 filter row full-width, 768–1023 compact view/save rows, <768 two-column fields + collapsible filter drawer + wrapped chips + stacked canvas. Playwright screenshots captured at 375/430/768/1280/1440 (`.ui-qa/phase-b/`, gitignored); no horizontal overflow or console errors at any width. |
| 2026-11-16 | B4 | Outreach chain browser pass. Fixed pan-captured link clicks (setup/message cards), valueless `hx-post` (405s), and stale board refresh after panel posts; added `/outreach` HX region fragment + test. |
| 2026-11-16 | D2 | axe-core 0 violations (1440/375, filters + panels), Lighthouse a11y 1.0, contrast pairs ≥ AA, focus trap/Escape/backdrop/dirty confirm/keyboard map/reduced motion verified. Prospect form labels associated + test. |
| 2026-11-16 | E2 | Version bumped to `0.2.0` (minor: whole-app UI replacement). |
| 2026-11-16 | D3 | Independent review PASS 91/100 after fixing its blockers: 360px Filters panel, single-zone stage lens, hidden wire labels below 0.75, 49px toolbar, stacked flow <1024 (amendment). |
| 2026-11-16 | UX cleanup | Owner comments: toolbar fully responsive (49px 1024+, saved view hidden <1280, 119px phones); wire annotations and setup “connects to” text removed; card/setup/outreach gaps 12/16/12px; mobile cards keep meta/status; board-focused first load; corridor tightened 230→150px; copy pluralized. |
