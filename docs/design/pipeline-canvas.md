# Prospectly — "Pipeline Canvas" Redesign Specification
**Artifact path:** `docs/design/pipeline-canvas.md` (§9 design contract — every handoff brief references this path)
**Approved:** owner decisions 2026-11-11 (stage board + flow-step pages; freeform canvas metaphor), spec authored by `ui-design` (session ses_e33218438ffeMMCNavEpP5XSif), orchestrator adjustments noted below.
**v2 (2026-11-12):** owner decision — *canvas-only app*: "remove all old pages from the experience, make the Canva style the main style; every setup gets its own card on the canvas that opens into its own pipeline setup and connects to other pipelines." See **§V2 addendum at the end of this file — it wins on any conflict with the base spec.** Design research grounding: `docs/design/references-canvas.md` (Figma shell/zoom grammar, Attio board density, Linear keyboard parity + avoid-list).

## Pipeline stage status (ui-engineering-standards §1)
| Stage | Status |
|---|---|
| Product understanding | Done — owner decisions + scope split (canvas domain vs conventional domain, §0.1/§5) |
| Design research | See `docs/design/references-canvas.md` (2–3 live references, URLs + retrieval dates, take/not-take, avoid-list) |
| UX architecture | Covered herein: journeys/interaction model §6.2, IA + navigation §1/§5, screen-state inventory §2.4/§3.1/§4 |
| Visual direction + design system | `charcoal + signal orange` tokens (app.css :root, unchanged); direction + avoid-list in `docs/UI_UX_SPECIFICATION.md` opening/§10 |
| Implementation | A1–A4 + B1–B4 built (2026-11-12→16): canvas shell/engine/board, nine setup cards, outreach region, panels, 360px Filters panel, saved-view toolbar, removable filter chips and single-zone stage lens. Responsive: stacked flow <1024 (amendment), 52px toolbar at 1280/1440. Outreach chain pass fixed pan-captured link clicks, panel `hx-post` targets (405s), stale region refresh and invisible redirect flashes. Version **0.2.0**. Test gate **378 passed / 0 failed**; lint/format/types clean. Tracker: `docs/design/canvas-implementation-tasks.md` |
| Browser validation | **Done** — 375/430/768/1280/1440 screenshots (`.ui-qa/phase-b/`, gitignored); axe-core 0 violations (1440/375, filters + panels), Lighthouse a11y 1.0, contrast pairs ≥ AA, focus trap/Escape/backdrop/dirty confirm/keyboard map/reduced motion verified (§V2.8) |
| Independent visual review | **PASS — 91/100** (2026-11-16, independent reviewer, no blockers). Residual non-blocking notes: default desktop fit 54–62%, mobile setup list could compress further |
| Refinement / approval | Gate passed; residual polish tracked in the task tracker |

> **Amendment 2026-11-16 (post-review):** the stacked flow applies below
> **1024px**, not 768px — at 768 the full world cannot fit at the 0.5 zoom
> floor, so tablet portrait gets the readable stacked flow. Filters and saved
> views live in the contracted **360px Filters panel**; the toolbar is one
> compact row (heading · Filters · chips · saved view · Add prospect). Card
> cards are 84px minimum with 16px gaps; density stays full at board fit (floor 0.75).
> Wire annotations and setup→pipeline connectors are removed (only
> orthogonal stage-flow connectors remain); setup cards show title + state
> only. `?stage=` keeps every lane visible and dims the non-matching ones
> (no auto-zoom above 125%). First load fits the **board width** with a
> minimum 0.93 scale (pan instead of shrinking); selecting a card pans it
> beside the drawer at 100–125% zoom. Phones use a 2-column setup grid and
> keep saved views in the panel.
> **Owner request 2026-11-16:** plain wheel zooms at the pointer and
> Ctrl/Cmd+wheel pans (§2.2).
>
> **Borderless / label pass (2026-11-17, owner directives):** no element
> outlines — cards, zones, panels, badges, inputs and buttons read from the
> shade ladder (`bg → surface → card → raised → field`); `--color-border` is
> internal dividers only. The prospect card's stage pill is a **sunken corner
> tab** welded to the card edge (radius matches the card, no border) and card
> chips are neutral — colour only for suppression/failure/overdue signals.
> Self-evident card section labels were dropped (headings remain only where
> they group controls; panel titles are `<h2>` for heading order). The
> suppression banner sits below the sticky head, danger-zone status rides in
> the block head, outreach shows state row + message preview, activity
> payloads render as readable text. Drawer motion: `--dur-slow` 320ms with a
> decelerating ease. Verified: lighthouse a11y 1.0 on board and detail,
> 0 console errors across 7 routes, 392 tests green.
>
> **Owner pass 2026-11-17 (part 2):** an **Arrange** control in the canvas
> cluster (`data-canvas="tidy"`) forgets every dragged zone position, re-runs
> the default layout and frames the world; `resize` always redraws the wires
> and, on crossing the 1024px stacked/wide breakpoint, rebuilds the layout the
> saved coordinates were made for (fixes scattered connectors after
> mobile→desktop resize). Cards grew to 84px with 16px gaps / 12px zone
> padding, setup cards to 248px columns with 24px gaps and a fill + 1px-lift
> hover (no underline anywhere on canvas links). The active-filter band moved
> out of the toolbar to a sibling row below it. The drawer gained a **Session**
> group label with a divider, and nav items mark the current screen (`Home`
> on /prospects, `Messages` on /outreach; view clicks sync in JS). Head tags
> ride the right edge in detail and message panels. Copy is em-dash free
> (verified on rendered HTML) and trimmed of repeat/empty-state lines.

> **Phase 5–6 UX (2026-11-16):** detail/settings/message drawers share 560px
> with a sticky header; only Import keeps numbered steps; comma-separated
> fields get chip previews; usage caps are progress bars; the duplicates
> feature is named **Duplicates** everywhere. Links are blue (`#7aa2ff`),
> filter/phase/region chips are neutral, and destructive buttons are outlined
> by default — orange is reserved for the primary CTA and selected states.
> Body/micro text floor is 12/11px.
>
> **Settings-panel content grammar (2026-11-16):** setup drawers use one
> pattern — inset 12px-radius cards, a small section head, and
> hairline-separated rows. Wide status data leaves the table: Providers is a
> grouped row list (role heading, provider, configured/enabled flags, one
> used-today ratio, error only when present); Saved Views is a row list
> (name/date, filter chips, Load/delete) with a compact create callout; Profile
> is two cards (fields form, Discovery) with the prerequisite stated once.
> Tables remain only where they fit (General daily caps, Retention).

Status: implementation-ready · saved from ui-design session ses_e33218438ffeMMCNavEpP5XSif
Orchestrator adjustments applied: (1) rail group dividers = plain hairlines with title tooltips (no clipped text); (2) `g s` → /settings/profile (no /settings index exists); (3) engineer must verify CSRF header acceptance before relying on X-CSRF-Token.

## 0. Direction
A freeform pan/zoom canvas (Canva metaphor) on which the stage board is drawn as the default arrangement — Nexus-Pipeline-style numbered stage zones (01→03) joined by orange flow arrows, prospect cards as compact DOM cards inside zones, details/message review/filters as slide-over panels. Dashboard keeps its numbered flow-card identity as the entry point. Visual language: shipped charcoal+orange tokens (app.css :root), mono micro-labels for data groups, **shade-contrast levels instead of outlines** (borderless pass 2026-11-17 — `--color-border` is dividers only), one orange accent.

### 0.1 Tension resolutions (binding)
| Tension | Resolution |
|---|---|
| Freeform canvas vs rigid board | Zones are freeform (8px grid snap + alignment guides, client-persisted); cards are strictly zoned — a card always lives in a zone stack; dragging a card is ALWAYS a stage transition (server-validated), never free placement; drop outside any zone = snap back. |
| Freeform vs shareable URLs | Arrangement (zoom/pan/zone positions/card order) = localStorage only. Filters, saved views, stage lens = URL query params, back-button safe. |
| Canvas vs mobile triage | <1024px the canvas does not exist: same DOM renders as stacked flow (zones in canonical order, sticky headers, no transform layer, no pan/zoom/pinch). Mobile job = approve/confirm/follow-up triage. |
| Panels vs forms/tables | Only review/detail surfaces become panels. Settings×7, merge, import, usage, login, and create/edit forms = conventional pages/panels per §5. |

## 1. App shell
Persistent 56px icon rail (≥768px) + full-bleed canvas viewport + 52px per-screen toolbar row inside the viewport (53px with the bottom hairline; the rail head matches so both borders meet). No global top bar ≥768px.

### 1.1 Rail (replaces labeled .sidenav)
- 56px fixed left, full height, bg --color-bg, right hairline --color-border.
- Top: brand dot (12px #ff6b1a square, --radius-sm) linking /. Wordmark only in mobile header + login.
- Group dividers: plain 1px hairlines (aria-hidden) with title tooltip = group name (WORK / SETTINGS). No text rendered in the rail itself.
- Items — WORK: Dashboard /, Prospects /prospects, Outreach /outreach (new to nav), Merge Duplicates /merge/duplicates. SETTINGS: Profile, Providers, Usage, General, Import, Saved Views, Retention. Bottom pinned: Shortcuts (? help), Sign out (POST /logout form with csrf_field).
- Item anatomy: 40×40 hit area, 16px stroke icon (24px grid, stroke-width 2, currentColor), --color-text-muted; hover → --color-surface-hover bg + --color-text; active → --color-accent-light bg + 1px --color-accent-border + icon --color-accent + --glow-accent.
- Active-state exact-path conditions from current base.html preserved verbatim. title attr + CSS tooltip [data-tip] on --color-surface-raised --fs-xs 120ms delay (opacity only, no JS).
- Focus: 2px --color-accent outline offset 2px (global rule).

### 1.2 Canvas toolbar (per canvas screen, 52px row + 1px hairline, --color-surface)
Ownership: title group (h1 22px semibold + Filters) left; view/action group (Saved view micro-label + select, Manage, Add prospect) right, 16px between controls and 24px before the primary action.
1. h1 page title (Prospect pipeline / Message pipeline) — the accessible page heading.
2. Filters button (opens the 360px panel) with a neutral count badge when filters are active.
3. Saved views native <select> behind an uppercase "SAVED VIEW" micro-label (existing apply endpoint; "Manage" → /settings/saved-views). Hidden <1280px, where the Filters panel owns saved views.
4. Add prospect .btn.primary.
5. Pending-review chip (critique P1, 2026-11-17): when ≥1 message sits in
   `pending_review`, the toolbar shows a `Pending review · N` button (warn dot,
   `data-canvas-view="outreach"`) between the spacer and the primary action.
   Clicking it activates the Messages view (`.active` + `aria-current` + h1 +
   `replaceState`), so the queue — which sits off the default fit — is always
   one click away. Hidden when N = 0.
6. Active filters render as a full-width band under the row: --color-bg, 1px top hairline, 12px vertical padding, uppercase ACTIVE label, 32px removable chips with 13px text and 8px gaps, then a hairline-separated Clear all / Show all zones text action. Nothing when unfiltered. Below 1024px the band's separators drop when the clears wrap.

### 1.3 Mobile shell (<768px)
Unchanged: 52px header + hamburger off-canvas drawer (existing app.js logic); drawer keeps text labels.

## 2. Canvas engine
New: app/static/js/canvas.js, app/static/css/canvas.css (tokens only; constants ZONE_W=264, ZONE_GAP=48, ZONE_HEAD=36, CARD_H=72, CARD_GAP=12, ZONE_PAD=8, GRID=8 mirrored in CSS).

### 2.1 Layers
- Viewport fills the remaining height under the content-driven toolbar (no fixed calc; the active-filter band adds height only when filters exist).
- World layer .canvas-world: transform translate(panX,panY) scale(zoom); transform-origin 0 0; NEVER transitioned.
- Paint order: <svg class="canvas-wires" aria-hidden="true"> (stroke --color-border 1.5px, <marker> arrowheads) → zone frames (<section> absolute) → cards (normal flow in zone body; absolute only mid-drag).
- DOM order = canonical stage order regardless of visual arrangement (a11y contract: tab/SR order follows pipeline).

### 2.2 Pan/zoom
| Input | Behavior |
|---|---|
| drag empty canvas | pan (Pointer Events + setPointerCapture, grab cursor) |
| wheel | zoom at cursor |
| Ctrl/Cmd+wheel | pan deltaX/deltaY (trackpad pinch also arrives as ctrl+wheel) |
| touch two-finger | disabled <1024 (no transform layer); ≥1024 tablets same synthetic path |
| keyboard | +/= in, − out, 0 reset 100%, f fit; step ×/÷1.25 |
- Zoom clamp 0.5–2.0. Anchor = cursor (wheel) or viewport center (keys/buttons).
- Control cluster bottom-right fixed: vertical pill, --color-surface-raised, hairline, --shadow-1, --radius-lg: [−] [zoom% button = reset] [+] [⤢ Fit]; real buttons, aria-labels, mono --fs-xs tabular readout.
- Minimap: none (decided against).
- prefers-reduced-motion: pan/zoom are direct manipulation — instant, correct.

### 2.3 Stage zones
- One zone per stage, canonical order (SYSTEM_ARCHITECTURE §3.2): new → researched → opportunity_identified → outreach_ready → contacted → replied → follow_up_due → interested → converted, plus Terminal zone (not_interested / do_not_contact / archived — one zone, three sub-badges, no outgoing arrow).
- Four phase bands: 01 QUALIFY (4) · 02 OUTREACH (3) · 03 CONVERT (2) · TERMINAL (1). Phase chip: 24px square, accent-light bg, accent text, mono, radius-sm. Phase label row 36px above its band.
- Default: horizontal row per band, 48px gaps; first load = fit-to-content capped 1.0.
- Zone drag: pointer on frame/header only; 8px grid snap; 1px dashed --color-border-strong alignment guides at ±4px edge/center alignment; guides not persisted.
- Card stacks: 16px gaps (`--card-gap`), zone padding 12px; zone body max-height = 4×(CARD_H+CARD_GAP) then internal scroll.
- Wires: straight right-mid → next canonical left-mid; recompute rAF-throttled on zone drag.

### 2.4 States
- Empty board: centered EmptyState ("No prospects yet. Add one, or set up your profile to enable discovery." + Add your first prospect + Set up profile); zones not rendered.
- Empty zone: mono --fs-xs subtle "· none" (never a dashed drop placeholder).
- Loading: 3 static skeleton cards per zone (--color-surface-hover, radius-md, no shimmer); hx-indicator unchanged.
- Error: page-level flash banner with request ID; last-known board stays.

### 2.5 Density (zoom-linked)
- density-full (zoom ≥0.75): 84px cards (name/meta/flags).
- density-compact (<0.75): 40px (name + dots); meta row hidden. Board fit lands 0.75–1.0 on laptops (full cards); whole-world fit and narrow canvases switch to compact.

### 2.6 localStorage
Key pl.canvas.v1, one JSON blob, version in-band:
{ version:1, boards:{ prospects:{zoom,panX,panY,zones:{stage:{x,y}},cardOrder:{stage:[ids]}}, outreach:{…zones:pending_review/approved/opened_in_whatsapp…} } }
- Write debounce 500ms; quota errors swallowed. Read on init; version mismatch/parse failure → discard → defaults.
- cardOrder pruned to server payload IDs each load; cap 500/zone (beyond → server order); unknown IDs append bottom. Multi-tab last-write-wins (documented contract).

## 3. Cards & nodes
### 3.1 Prospect card (<article>, 84px min, card fill #1f232a, no outline, radius-md, 16px inset via `--card-pad`)

> **Stage-tab scope (owner decision 2026-11-17):** the fused corner tab renders
> **only inside the Terminal frame** (not_interested / do_not_contact /
> archived — three stages, one zone), where it carries information. In its own
> lane the zone header already names the stage, and long stage names truncated
> inside a tab that repeated it. `board.js` guards a missing badge, so optimistic
> stage moves stay safe on cards without the tab.

1. Row1: business_name 13px fw-semibold truncate; right: 6px status dot — --color-warn follow-up due/overdue, --color-accent requires review; never color-alone (paired with flags row text).
2. Row2 mono --fs-xs muted tabular: category · city ("none" as subtle — P6 honesty).
3. Row3: StatusBadge vocabulary at --fs-xs: stage, website-status (no_website_found = info, never red — P6), Suppressed/Archived, ai origin accent on active message (P5).
4. Row4 (only when meaningful): relative last-contact "3d" + follow-up "due 2d" mono; Open WhatsApp icon button ONLY when message status=approved (data-confirm text verbatim; P1 — no other send-ish control ever on cards).
States: default(hairline) / hover(surface-hover + border-strong) / focus-visible(2px accent + glow) / selected(accent-border + glow while panel open) / dragging(absolute at pointer, scale 1.02, shadow-2, 90% opacity, no transition) / ghost(dashed border-strong, correct height) / rejected-409(snap back 120ms ease-out + danger toast with server msg) / drop-target(accent-light bg + dashed accent-border + mono --fs-xs "Move to ⟨stage⟩" label — fill+label because border alone is 2.17:1 < 3:1).
Interactions: click/Enter → detail panel; drag → stage transition; m (or card ⋯ button, focus-reachable) → Move-to menu role=menu on surface-raised/shadow-2 listing server-rendered allowed next transitions (same whitelist as detail select); arrows+Enter → same POST as drag.

### 3.2 Zone header (36px)
Phase chip (on band, not every zone) · stage label mono uppercase --fs-xs tracking-label text · count mono tabular muted right (0 → subtle). Hot count dots warn/accent.

### 3.3 Flow-step nodes
Dashboard: existing 01→02→03 flow cards + "→ Open pipeline" per phase linking /prospects. Board: chips in band headers; wires carry arrows (aria-hidden; numbers/order carry meaning). Outreach: 01 PENDING REVIEW → 02 APPROVED → 03 OPENED IN WHATSAPP.

### 3.4 Message card
prospect name, first 60 chars current_text (muted), status badge, mono age. Footer (server-rendered): Review (pending_review) → message panel; Approve+ack checkbox live IN THE PANEL only; Confirm sent / Not sent (opened) with existing data-confirm. P1–P3: nothing posts a message anywhere from a card.

### 3.5 Orange whitelist on canvas
Only: phase chips; selected/focus rings; requires-review dots + ai badges; Add prospect primary; drop-target fill/border mid-drag. Never: zone fills, card fills, wire color, stage badges, counts.

## 4. Panel system
New app/static/js/panel.js; <div id="panel-root"> in base.html. Panels = modal dialogs all breakpoints (role=dialog aria-modal=true aria-labelledby).
- Widths: Filters 360 · Detail 480 · Create/Edit 420 · Message 560; width min(Wpx, 100vw−72px).
- Right slide-over, full height, surface bg, left hairline, shadow-2; slide translateX(24→0)+fade --dur-base 150ms; reduced-motion static.
- Backdrop rgba(0,0,0,.2) (canvas legible behind); canvas container gets inert while open; backdrop click closes topmost.
- Header 48px sticky: title, badge row, contextual action, X (44px hit). Content scroll below.
- Stacking: two levels ONLY ≥1400px (detail 480 + editor 560; editor z-above; stacked class drops detail backdrop). <1400px editor replaces detail in place with ← Back to prospect (cached detail, no refetch). Filters never stacks (opening it closes detail).
- Focus: open → focus close button; Tab trap across topmost stack; Escape closes topmost (stopImmediatePropagation pattern like drawer; panel stack takes priority over help/drawer); close → focus returns to invoker.
- Close: X + Escape + backdrop always. Dirty form panels (editor, create/edit): Escape/backdrop only after window.confirm("Discard changes?") (beforeinput flag).
- Content: HTMX partials into #panel-body (hx-get). Content-negotiation branch on EXISTING GET routes — same handler returns panel partial when HX-Request (+HX-Target=panel-body), else full page. No new endpoints. Applies to: /prospects/{id}, /prospects/{id}/research, /prospects/new, /prospects/{id}/edit, /messages/{id}/edit. Form posts in panels: hx-post, 422 re-render lands in panel, success swaps + existing HX-Trigger toast + card updated in place.
- Mobile <768: full-screen sheets, slide-up (translateY 24→0), 52px header, close left, 44px targets, no stacking (editor replaces detail with ← Back).

## 5. Page mapping
| Route | Treatment |
|---|---|
| / dashboard | conventional page (kept; phase cards link into board) |
| /prospects | CANVAS stage board (replaces table) |
| /prospects?filters… | canvas filtered; stage= = lens (matching lane highlighted, other lanes dimmed + "Show all zones" chip); saved views unchanged |
| /prospects/{id} direct GET | conventional page fallback (restructured detail kept) |
| /prospects/{id} card click | panel 480 |
| /prospects/new, /{id}/edit | panel 420 |
| /prospects/{id}/research (+status partial) | panel (polling partial unchanged) |
| /outreach | CANVAS outreach board (3 status zones); Outreach joins rail |
| /messages/{id}/edit | panel 560 (page fallback direct GET) |
| /messages/{id}/open + confirm/not-sent POSTs | unchanged flows, re-skinned entry points |
| follow-ups (not built) | reserved conventional page + rail item when built |
| /merge, /settings×7, /login | conventional pages inside rail shell |
| /healthz, /api/v1/* | unchanged |
No new endpoints; only content-negotiation branches on existing GETs.

## 6. Responsive + a11y
### 6.1
| Region | ≥1024 | 768–1023 | <768 |
|---|---|---|---|
| Shell | 56px rail | 56px rail | header + drawer (existing) |
| Canvas | full board, density-full ≥0.75, fit on load | stacked flow: canonical zones, sticky headers, density-full always, no transform/zoom cluster | same stacked flow |
| Panels | widths; stacking ≥1400 | widths clamped, no stack | full-screen sheets |
| Cards | hover quick-actions | same | actions always visible, 44px targets |
| Tables (conventional) | all columns | existing expand | existing stacked rows |

### 6.2 Keyboard map
? help (content updated) · / opens Filters panel focused search on canvas screens · j/k move highlight in DOM order (zones canonical) · Enter open panel · m Move-to menu · +/−/0/f canvas zoom · g d / g p / g o / g s (→ /settings/profile) go-to via sequence buffer · Escape: panel → move menu → help → drawer (each layer stops propagation). Ignored while typing (existing guard). Every shortcut has a pointer equivalent.

### 6.3 ARIA
Exactly one `aria-current` per page: the SSR-marked rail view (`/` → Home, `/prospects*` → Board, `/outreach` → Messages) and — on view-button clicks — `canvas.js` moves `.active` + `aria-current` together, rewrites the h1 (Prospect pipeline / Message pipeline) and `history.replaceState`s the matching path (query preserved), so URL, heading and screen-reader state always agree. Canvas section aria-label="Pipeline canvas"; world = presentation. Zone section role=region aria-labelledby; header h2 "New — 3 prospects". Card article + h3 name + live buttons; drag never the only path (Move menu = parity). Wires + chips aria-hidden. Panels dialog/labelledby/trap/inert-on-canvas. aria-live polite: "Card moved to ⟨stage⟩", 409 reason, "N cards match filter". Zoom buttons aria-label. Contrast pairs (all AA): text 15.08, muted 7.70, subtle 5.58, accent 6.36, link 7.72, controls ≥3:1 (#6a7280), drop-target fill under text ≈13.4; dots always paired with mono text.

## 7. Spec amendment text (paste-ready for docs/UI_UX_SPECIFICATION.md)
Opening: "Design direction (amended 2026-11-11 — Pipeline Canvas redesign). The app's primary work surfaces are now a freeform canvas: a pan/zoom world (0.5–2.0×, vanilla-JS transform layer, DOM cards — never bitmap canvas) on which the prospect pipeline is arranged as a stage board — numbered phase bands (01 QUALIFY → 02 OUTREACH → 03 CONVERT → TERMINAL) containing stage zones with compact prospect cards, joined by presentation-only orange flow arrows. Zones are freely placeable (8px grid snap + alignment guides, client-persisted); cards are rigidly zoned — dragging a card is always a stage transition validated by the server (POST /prospects/{id}/stage; illegal transitions 409 → toast → snap-back), and drag never performs any outreach action (P1–P3). Detail, research, message review/edit, filters and prospect forms open as slide-over panels (modal dialogs with focus trap) over the canvas; settings, merge, import, usage and login remain conventional pages. The dashboard keeps its numbered flow-card identity as the entry point to the canvas. Visual tokens are unchanged (app.css :root); new canvas constants live in canvas.css/canvas.js. <1024px the canvas degrades to a stacked flow (zones stacked in canonical order, no pan/zoom) because mobile is review/light-triage, not spatial manipulation."

§1 second half: grouped sidebar replaced by 56px icon rail ≥768 (same tree + Outreach newly linked); <768 keeps drawer; active-state exact-path unchanged; tooltips + ? help carry names.

§2 rewrites: 2.1 dashboard unchanged responsibility + phase cards link onto board. 2.2 Prospects = "scan, filter, triage on the stage board"; table retired; filters/saved views = Filters panel, URL semantics unchanged; stage= = lens; quick actions on card hover/footer with same gating (no bulk outreach — P1). 2.3 detail = dual rendering (panel via HTMX + page fallback); panel section order: journey strip → key facts → stage transitions (server whitelist unchanged) → suppression → research → opportunities → outreach chain → activity; forms/behaviors identical. 2.4 outreach = second canvas view; approval+ack happens in message panel, never on a card. 2.7/2.8 unchanged.

§7 table: as §6.1 above.

§10: drag-and-drop exclusion amended — binding drag rules: (1) card drag only as stage-transition gesture to existing server-validated endpoint, 409 → revert + toast; (2) no bulk drag operations; no drag can ever trigger WhatsApp send/draft/approval (P1–P3); (3) zone-frame drag = layout-only, client-persisted, server never sees it; (4) keyboard Move-to menu mandatory parity for every drag; (5) cards cannot be placed on open canvas. Other exclusions unchanged.

## 8. Implementation plan
### 8.1 Files
New JS: canvas.js (world/pan/zoom/clamps/density 0.75/rAF wires/localStorage pl.canvas.v1/zone drag snap+guides), board.js (card drag 4px threshold/ghost/hit-test/optimistic move + POST /prospects/{id}/stage with CSRF/409 revert+toast/cardOrder persistence/Move menu), panel.js (dialog stack/HTMX loading/focus trap/Escape priority/≥1400 stacking/dirty confirm/mobile sheet). New CSS: canvas.css (viewport/world/zones/cards/panels/cluster/stacked-flow media; tokens only).
Templates: base.html (rail swap, #panel-root, <meta name="csrf-token" content="{{ csrf_token(request) }}">, new script/css includes). New partials: canvas_board.html, card_prospect.html, card_message.html, canvas_outreach.html, panel_detail.html (extracted from detail sections), panel_message.html (from messages/edit), panel_filters.html (from list filter bar + save-view form). Rewritten: prospects/list.html → thin wrapper over canvas_board.html; detail.html + messages/edit.html keep full-page markup by INCLUDING the same section partials (dual rendering, one source). Retired: prospects table markup + row data-href; app.js j/k retargets to cards. Untouched: login, settings/*, merge, all message POST flows, services; only server touch = GET content-negotiation branches in pages.py (listed routes) on HX-Request.
### 8.2 Build order (shippable each step)
1. Shell + panels (rail swap, panel system, csrf meta, detail/message open as panels over existing table). 2. Canvas engine + Prospects board read-only. 3. Drag-to-stage + Move menu + cardOrder persistence (server-validated writes, 409 toasts). 4. Outreach board + message panel. 5. Filters/saved views/lens + dashboard entry links. 6. Mobile stacked flow + keyboard map + a11y + QA.
### 8.3 QA proof points
(1) drag legal → 200 + badge/order update; illegal → 409 toast + snap-back; offline → revert; drag never POSTs to send/approve/open endpoints. (2) keyboard-only full journey. (3) URL filter/share/back/forward + saved view + lens. (4) localStorage reload + fake version:99 discarded + 501-id cap fallback. (5) panels: trap/escape/backdrop/dirty/422-in-panel/stacking/←Back. (6) 390px stacked flow + sheets + 40px targets + triage flows. (7) reduced motion static + functional. (8) contrast computed pairs. (9) CSP clean under shipped headers. (10) pytest/ruff/mypy green.
### 8.4 Risks
1 tab/SR order ≠ visual → DOM canonical + zone headings + Move menu. 2 optimistic drag vs 409 → snapshot/re-insert revert + server reason toast + tests. 3 CSRF for JS POSTs → verify middleware header acceptance; meta tag from csrf_token(); flag in SECURITY_AND_PRIVACY §9 checklist notes. 4 localStorage growth → 500 cap/prune/content-visibility/scroll cap/fit. 5 dual-render drift → single partial sources + grep test + fallback page = superset.

---

# §V2 addendum (2026-11-12) — canvas-only app: setup cards + mini-pipelines + fixed wires
**Supersedes base §0.1 (panels-vs-forms row), §1 rail item tree, §2.6 localStorage shape, §5 page mapping, §8 build order on every point named here. Base spec remains authoritative for: engine mechanics (§2), card anatomy states (§3.1), panel system (§4), a11y/contrast (§6), drag rules (§7 §10 amendments).**

## V2.1 The single world (one canvas rules the app)
The app has exactly **one screen**: the canvas world. It contains three region types placed freely (8px snap + alignment guides, client-persisted):
1. **Prospect stage board** — the base-spec zones/bands/cards (unchanged mechanics), the dominant region.
2. **Setup-card cluster** — nine canvas cards (§V2.3), default-placed in a row above/left of the board.
3. **Outreach message region** — the base-spec three message zones (pending_review → approved → opened_in_whatsapp), placed right of / below the board's 02 OUTREACH band.

**Fixed wires** (read-only system map, SVG in the wires layer, 1.5px `--color-border`, aria-hidden; no midpoint labels — the nine setup cards carry their own titles/state, per owner request 2026-11-16):
| From → To | Label |
|---|---|
| Profile → Board | discovery + drafts |
| Import → Board | adds candidates |
| Providers → Board | research + drafts |
| Usage → Providers | monitors |
| Retention → Board | flags inactive |
| Merge → Board | dedupes |
| Saved views → Board | filters |
| General → Profile | defaults |
| Board (02 OUTREACH band) → Outreach region | messages in flight |

Wires recompute rAF-throttled with zone/card drags. Wires are decoration-for-explanation only: nothing a wire depicts is a runtime dependency the user can change (no rewiring, ever — owner decision).

## V2.2 Old pages removed from the experience (routes kept)
- The canvas is the only navigable screen. `base.html` rail loses the settings tree (§V2.4).
- **Plain GET** of any retired page route (`/`, `/settings/*`, `/merge/duplicates`, `/outreach`, `/prospects`, `/prospects/{id}`, `/prospects/new`, `/prospects/{id}/edit`, `/messages/{id}/edit`, `/prospects/{id}/research`) returns the **canvas shell with that card pre-opened** (server-rendered `open_card` context + panel content embedded open). Same paths, same 200s, deep links land on the canvas with the right card open.
- **HX-Request** on the same routes → the panel fragment only (base §4 branch, unchanged).
- **Login stays standalone** (security gate, no chrome). `/api/*`, `/healthz`, static unchanged.
- Old page templates are retired *as pages*: their inner content moves into card-panel partials (dual-render rule applies: one partial source, page-wrapper markup like `page-bar` headers deleted). Tests asserting retired structure are updated to the new contract — every test change reported with justification.

## V2.3 The nine setup cards + their mini-pipelines
Each card: compact canvas card (base card anatomy minus stage rows): 16px stroke icon, title (13px semibold), one-line live state (mono `--fs-xs`, e.g. `caps 0/1000 · backup never`, `needs attention: 3`), warn dot when actionable. No `connects to …` line (owner request 2026-11-16 — title + state only). Click/Enter → panel (base §4 widths: 480 default) containing a **mini-pipeline**: 3–5 connected step-cards laid out horizontally (stacked in the mobile sheet), numbered `01…0n` (Nexus grammar), arrows between; each step is a collapsible section holding the card's actual real content (existing forms/tables/stats — no new features, no fake data):

| Card | Mini-pipeline steps (content source) |
|---|---|
| **Status** (was Dashboard) | Needs attention → Pipeline metrics → Quick start (existing dashboard blocks; the 01→03 pipeline strip is dropped — the board itself carries phase chips and counts) |
| **Profile** | Offer → Target industries/locations → Signals → Discovery (run button + recent-runs table at the flow's end) |
| **Providers** | Discovery (BizData · OSM) → Search (Tavily · Serper · DDGS) → Fetch (SafeFetcher) → AI drafts (Gemini · Groq · OpenRouter · Ollama · Template) — existing status rows grouped per step |
| **Usage** | Provider calls → AI tokens → Cap status (existing tables/strip) |
| **General** | Vocabulary → Defaults → Daily caps → Backup |
| **Import** | Upload → Preview → Commit (existing wizard, unchanged logic) |
| **Saved views** | Filters → Named views → Apply |
| **Retention** | Threshold → Sweep → Flagged → Clear |
| **Merge** | Threshold → Candidate pairs → Merge |

- **Outreach is a canvas region, not a card** (message cards per base §3.4; approval + ungrounded-ack stay inside the message panel).
- Status card carries a `--color-warn` dot when the needs-attention queue is non-empty (server-rendered).

## V2.4 Rail v2 (replaces base §1.1 item tree)
56px rail, same anatomy/active/tooltip rules. Items only: **brand dot** → `/` (fit-to-home), **Home** (`fit` whole world), **Board** (fit prospect board region), **Outreach** (fit outreach region), **Status** (opens Status card), divider, **Shortcuts** (`?` help), **Sign out** (existing POST /logout form). Settings items are gone — cards own them. Keyboard: `g h` home, `g p` board, `g o` outreach, `g s` status (replaces base `g d/g p/g o/g s` set); `?` help updated.

## V2.5 localStorage (replaces base §2.6 shape)
Key `pl.canvas.v1`: `{ version:1, world:{ zoom, panX, panY, regions:{ board:{x,y}, outreach:{x,y} }, cards:{ status:{x,y}, profile:{x,y}, …(9) }, cardOrder:{ stage:[ids] } } }` — same debounce/prune/cap/version-discard rules as base §2.6.

## V2.6 Mobile (<1024) — stacked flow + card list
No transform layer (base rule). Screen = setup cards as a vertical list (tap → full-screen sheet with the mini-pipeline stacked vertically), then the board as the base-spec stacked flow, then outreach zones stacked. Drawer nav mirrors the rail items with text labels.

## V2.7 Build order v2 (replaces base §8.2; shippable each step)
- **A1 — shell + engine + board:** rail v2, panel system + csrf meta, canvas engine (pan/zoom/localStorage/wires layer), prospect board (zones/cards/wires per base), drag-to-stage + Move menu, world skeleton with setup-card cluster rendered (cards open their panels — Status card fully contented), route content-negotiation (§V2.2 branches).
- **A2 — cards + mini-pipelines:** all nine cards' mini-pipeline panels (extract content into partials, retire page wrappers), wires complete (midpoint labels removed), Status dot, `g` chords v2, `?` help update.
- **A3 — outreach region + polish:** message zone region + message cards + panel, filters/saved-views/lens toolbar, mobile stacked flow + card list, full keyboard map, a11y pass.
- Test-impact rule: 331 baseline must be restored green each phase; test edits only where the approved contract changed (§V2.2), each justified in the commit.

## V2.8 QA additions (extends base §8.3)
- Direct-URL landing: every retired route returns canvas + correct card pre-opened (test per route).
- Wire layer: recomputes on region/card drag; annotations removed (wires only, aria-hidden).
- Card loop: click → panel → content matches old page's content (dual-render grep test).
- No settings links anywhere in the rail/canvas chrome (grep).
