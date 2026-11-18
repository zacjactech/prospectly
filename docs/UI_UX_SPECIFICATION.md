# UI/UX Specification

Design direction: **"charcoal + signal orange" operations console** — a charcoal shade ladder (`#090a0c` → `#292d35`) where **levels come from fill contrast and element outlines are gone** (borderless pass 2026-11-17), one vivid orange accent (`#ff6b1a` family) reserved for primary actions, active states, focus and running/`hot` statuses, and monospace micro-typography (uppercase group labels, tabular numeric readouts, log-style timestamps) against a system sans body. Influences: Nexus Pipeline dark dashboard (surfaces, accent discipline, numbered stages, pipeline-log type), evo-team/Academy grouped sidebars, oNeX line icons, the black+orange MARKETING poster (one loud orange element per screen). Dark-only — no `prefers-color-scheme` variant. Explicitly *not*: HUD/gauge decoration, gradients, glassmorphism, animated counters, color-coded-everything.

Influences researched (2026): CRM UX best-practice literature (aufaitux, eseospace, designstudiouiux) emphasizing density + clarity over visual flourish; practitioner HTMX-vs-React write-ups confirming server-rendered CRUD patterns; Linear/Notion-style restraint (compact tables, keyboard-friendly filters, inline editing).

---

## 1. Information architecture & navigation

> **Superseded by Amendment V2 (2026-11-12) — canvas-only.** The grouped
> sidebar IA below is retired from the *experience* (routes and deep links
> survive). Read §V2 first; the rest of this section documents the pre-canvas
> tree that the canvas replaced.

### V2. Canvas-only IA (2026-11-12) — current

The app has **one screen**: the Pipeline Canvas
(`docs/design/pipeline-canvas.md` §V2 — binding contract). Three regions on a
single pan/zoom world, joined by a fixed, read-only wire layer that explains
what feeds what:

```
Pipeline Canvas (GET /prospects — the default surface)
├── SETUP region — 9 cards, 3 × 3 (each opens its own mini-pipeline panel)
│     Status · Profile · Providers · Import · Saved views · Retention
│     Merge · Usage · General
│     wires → board (discovery, drafts, research, adds candidates, filters,
│                flags inactive, dedupes); Usage → Providers, General → Profile
├── PIPE region — the stage board (phase bands + zones + cards)
│     zones are the real stages from SYSTEM_ARCHITECTURE §3.2; drag = a
│     server-validated stage transition only (409 on illegal, P1-safe)
└── MSG region — the outreach region (pending_review → approved → opened)
      wires ← board ("messages in flight")
```

**Rail (left):** Canvas group — Home / Board / Outreach / Status — then
Shortcuts and Sign out. The settings tree is gone from the rail: the setup
cluster *is* the index.

**Every setup has its own card that opens its own pipeline.** A card click
loads `GET <route>` over HTMX into a slide-over panel whose body is a numbered
mini-pipeline (`01 … 0n` steps, arrows drawn by CSS) holding the *same real
forms/fields/tables* as the retired page — nothing was simplified, only
re-framed. Panels are modal at every breakpoint (focus trap, Escape, backdrop,
dirty-form confirm, 2-level stacking ≥1400px).

**Deep links (§V2.2, amended 2026-11-18):** retired views no longer open as
panels over the canvas — they are standalone pages (owner: "views without the
canvas become tab-based"): `/settings/*` + `/merge/duplicates` share one
settings **tab page** (tab strip = navigation, deep link = active tab),
`/prospects/{id}` is a detail **tab page** (`#detail-*` anchor tabs under a
sticky strip), and `/status`, edit, research and message routes are generic
standalone pages. `HX-Request` still returns the same body fragment (panel
parity). Canvas routes (`/`, `/prospects`, `/outreach`) keep the shell; `/`
still lands with the Status card open, and the canvas Filters + Add-prospect
action panels still open over it.

**Mobile (<768px, §V2.6):** no transform layer — the world stacks vertically
in canonical order (setup cards → board bands/zones → outreach zones); wires
and zoom controls are hidden.

**Rail chords:** `g h` fit world · `g p` fit board · `g o` fit outreach ·
`g s` open Status · `?` help.

---

**Amendment (2026-11-11) — retired sidebar:** the app now ships a **grouped left sidebar** (WORK / SETTINGS), replacing the 2026-11-07 persistent top bar. Rationale: the live nav carries **10 sections** — past the ~8-section threshold this spec itself set for when a sidebar "pays for itself"; §7's responsive table already specified sidebar behavior (persistent ≥1200px, 56px icon rail 768–1199px, hamburger drawer <768px), so the top bar contradicted the spec's own responsive plan; and the moodboard-driven "charcoal + signal orange" redesign (see the opening) makes the grouped sidebar the strongest identity element while the dense table keeps ~all of its width. The IA tree below is what the sidebar renders, grouped.

```
Prospectly
├── Dashboard            (pipeline strip + "needs attention" queue)
├── Prospects            (list = home base; filters, saved views)
│     └── Prospect detail → standalone tab page (Overview · Research · Outreach · Activity tabs; sections scroll within)
├── Follow-ups           (due / overdue / upcoming)
├── Outreach             (message queue by status: pending_review → approved → opened_in_whatsapp)
├── Settings
│     ├── Profile        (offer, target industries/categories, locations, signals — drives discovery + drafts)
│     ├── Import         (CSV utility — secondary, not part of discovery flow)
│     ├── Providers      (keys/status, feature flags)
│     ├── Usage          (provider + AI usage, errors, rate limits)
│     └── General        (vocabulary, defaults, backup)
└── Docs (target state only — not in the sidebar; a docs surface must exist first, and Swagger at /api/docs renders as an empty shell under the CSP)
```

**Design rule:** the pipeline is never more than two clicks deep; every list page has one dominant primary action in the header (Add prospect / Import / Run research / Generate draft).

**Design system:** `app/static/css/app.css` is the single source of visual truth — a dark charcoal **shade ladder** (`bg → surface → hover/card → raised → field`) where every container, chip and control reads by fill level instead of an outline; `--color-border` survives only on **internal dividers** (rows, tabs, regions) and `--color-border-strong` only on focus/error boundaries (≥3:1, WCAG 1.4.11). Orange primary button, orange reserved for accent roles only (actions, active nav, focus, running/hot statuses, AI-origin badges), compact 13px table rows, mono uppercase micro-labels **for data groups only** (card section headings were dropped 2026-11-17 — content speaks for itself; headings remain where they group controls) and mono tabular numerals for all readouts, semantic badges as dark tint + colored text (icon/word always present — never color alone). Behaviors live in `app/static/js/app.js` (CSP forbids inline handlers): mobile nav drawer, `data-confirm` prompts, whole-row navigation.

## 2. Pages & responsibilities

> **V2 (2026-11-12):** each "page" below is now a **card body** rendered inside
> the card's mini-pipeline panel on the canvas (`app/templates/canvas/cards/*`
> for the nine setup cards; `partials/panel_*` for prospect/message/research
> detail). Responsibilities, fields and flows are unchanged — only the surface
> moved from a full page to a panel. Section 2.4 (Outreach queue) is the
> outreach *region* on the canvas; 2.5 (Follow-ups) is reached from the
> Status card's attention queue; 2.1's pipeline strip is dropped (the board
> carries stage counts with phase chips).

### 2.1 Dashboard
- **Responsibility:** answer "what do I do next today?" — not analytics theater.
- **Sections:**
  1. *Needs attention* queue (overdue follow-ups, drafts pending review, research runs failed, findings unverified) — the primary block, sorted by urgency. Empty state: "Nothing needs attention. ✓"
  2. *Pipeline strip*: counts for every stage, grouped into three labelled display phases so the numbers read as a progression — **Qualify** (new, researched, opportunity, ready) · **Outreach** (contacted, replied, follow-up) · **Convert** (interested, converted). Each phase is its own shaded card of stage cells (not a funnel chart, not a single undifferentiated row); since the 2026-11-11 pipeline-style refresh the cards are numbered **01–03** with a phase-total in the header and joined by orange flow arrows (presentation-only decoration — the numbers carry the order, so nothing depends on color or arrow direction). Phase grouping is presentation-only: stage order and semantics come from `SYSTEM_ARCHITECTURE.md` §3.2 and are unchanged.
  3. *Today*: follow-ups due today + recently replied.
  4. *Metrics groups*: the nine operational metrics (`PRODUCT_BRIEF §8`) arranged as **three themed cards** — **Pipeline** (Total, Researched, Requires review) · **Outreach** (Approved, Sent, Replies) · **Results** (Interested, Follow-ups due, Converted). Each card is a shaded surface with an uppercase mono group label and left-label / right-value rows (tabular numerals). Empty values are dimmed; actionable backlogs (Requires review > 0, Follow-ups due > 0) read in the accent (`hot` role, redesign 2026-11-11). Cards sit in one column on mobile, three columns from 640px. Grouping is presentation-only: the metric definitions come from `app/services/dashboard.py` and are unchanged.
- All numbers link to the pre-filtered Prospects list.

**Status (Phase 5):** the **pipeline strip** and all **nine metrics groups** are built and rendered honestly — real per-stage counts of non-archived prospects (`app/services/dashboard.py`, whose `PIPELINE_PHASES` defines the stage grouping and derives `PIPELINE_STAGES`), zero-filled and grayed when empty, every pipeline cell linking to `/prospects?stage=…`. The "Needs attention" queue is live. Empty dashboard: one sentence + Add prospect / Set up profile.

### 2.2 Prospects list (home base)
- **Responsibility:** scan, filter, triage.
- Dense table (rows ~40px): Name · Category · Location · Website status · Stage · Last contact · Next follow-up · Flags (duplicate? suppressed? unverified?).
  **Status (Phase 2):** Next follow-up and the "requires review" flag are Phase 4 columns and are not rendered yet (showing a permanent "none" column would be a lie); the rest are live. Website status and stage badges use `stage-<value>` color mappings, where `no_website_found` reads as neutral **info**, never as an error (P6).
- Filter bar: stage, category, tag, location, "requires review", archived toggle, free-text search. Filter state persists in URL (shareable/back-button safe). When any filter is active, an inline **Save current view** form (name + save) appears beside the bar; saved views apply from Settings → Saved views (`GET /prospects/views/{id}/apply` redirects onto the list with the view's whitelisted filters). **Canvas V2:** active filters also render as removable chips under the toolbar — each chip is a plain link preserving every other filter, and a stage filter exposes **Show all zones**; below 768px the filter form collapses behind a **Filters** toggle and chips wrap.
- Row click → detail. Hover reveals quick actions (Run research, Open WhatsApp — only when state allows).
- Bulk selection limited to safe ops: add tag, archive, change source. **No bulk outreach actions exist anywhere** (P1).
- Empty state: "No prospects yet — configure your Profile, run automated Discovery, or Add manually" + two buttons.

### 2.3 Prospect detail (standalone tab page, 2026-11-18)
Rendered as its own page (no canvas behind): page head = name + stage/category
tags + Edit details; a sticky tab strip (Overview · Research · Outreach ·
Activity) jumps to the shared sections (active state follows the hash; the
Outreach tab hides while suppressed). The shared partial keeps its panel head
in the DOM for fragment parity — page CSS hides it so the name renders once.

### 2.3.1 Sections
- **Header:** name, stage badge, key facts (phone with copy, email, website with "open" + status chip), suppression warning banner if `do_not_contact`.
- **Stage card:** allowed-next-transitions select only (canonical steps + `not_interested` from post-contact stages); anything else is rejected server-side with 409.
- **Suppression block (Danger zone):** no sub-heading (the Danger zone frame + status badge carry it). When not suppressed — "Block outreach" button with an optional reason input, and the explanation ("Blocks drafts, approvals and WhatsApp actions. Clearing requires a reason.") rendered **above the button** (confirm dialog names the prospect); when suppressed — reason + last-suppression context and a "Clear suppression" form with a **required** reason (Flow 8: clearing is explicit and audit-logged).
- **Section: Overview** — profile fields (inline edit), source, tags, notes (add/edit), next follow-up, primary actions (Run research / Generate draft / stage transitions).
- **Section: Research** — findings restacked as rows (2026-11-17; the panel is 560px, so no column ever scrolls horizontally out of reach): line 1 = statement (full width), line 2 = kind · verification · confidence · origin badges, source link (opens in new tab), and actions (Verify/Edit/Delete) right-aligned. Website check history sub-panel with outcome + signals. "Add finding" form.
- **Section: Opportunities** — cards *only here* (each is an evidence object): type, summary, rationale, linked evidence chips (click → jump to finding), Validate/Dismiss. Empty state explains the rule engine runs after research.
- **Section: Outreach** — message versions list (status timeline), current draft editor with character/line guidance, grounding panel (findings used), the gated action chain as **one primary button whose label reflects the next legal step**: `Submit for review → Approve → Open in WhatsApp → I have sent this message`. Secondary: edit, return to draft, start new outreach (guarded). After open: an explicit confirmation prompt "Did you send it?" with Yes/No — No returns to `approved`. AI-generated drafts carry an `ai` origin badge plus "verify every claim before sending" (P5); template drafts show `template`.
- **Section: Activity** — append-only timeline (icon, actor, description, timestamp; payload details expandable).

### 2.4 Outreach queue
- Kanban-free **grouped list** by message status: `pending_review` (needs me) → `approved` (ready to send) → `opened_in_whatsapp` (did I send?). Each row: prospect, first line of message, age, actions (Review / Open / Confirm sent). Purpose: never lose an approved-but-unconfirmed message.

### 2.5 Follow-ups
- Grouped: **Overdue** (red count) · **Today** · **Upcoming**. Rows: prospect, due date, note, last contact. Actions: Complete, Snooze, Open prospect. Overdue group uses restrained amber/red only on the count chip — no row backgrounds.

### 2.6 Import (CSV — secondary utility)

Moved under Settings because CSV is not the default discovery path. 3-step wizard: Upload → Preview (valid/duplicate/error sections with per-row reasons, row checkboxes) → Commit + result summary. Shows backup reminder before commit.

### 2.7 Settings
- **Profile:** your offer, target industries/categories, target locations, ideal-client signals — one editable form; drives automated discovery and personalizes drafts. Below it: **Automated discovery** card — a "Run discovery" button (queues a `discovery_job` + worker job; flash names the worker command) and a recent-runs table (status, candidates found, created, error).
- **Saved views:** list of named list-filters with an **Apply** link (redirects to the filtered list) and delete; create form accepts structured list filters (unknown keys rejected with 400).
- **Providers:** per provider: role, configured? (key present), enabled?, calls today, daily cap with "blocked" state, most recent recorded failure, and a note explaining when it is used (e.g. DDGS is the conditional fallback, Brave is config-only). Keys themselves are never displayed — only presence booleans. **Read-only (shipped 2026-11-11):** the enable/disable toggle and the "Test" button are deferred — the toggle would rewrite `.env` via `save_settings` (which drops the file's comments, a data-hygiene decision for the owner) and the Test button performs a live paid call from the UI, which needs a cap/consent decision first.
- **Usage:** table of provider calls/errors/rate-limits per day + AI calls/tokens per day, with a daily-cap strip (used / cap, "blocked" when reached). Simple table, no charts.
- **General:** category vocabulary editor, default follow-up days, daily caps with **used-today and a "cap reached — calls blocked" badge**, backup button + last-backup timestamp.
- **Import (CSV):** secondary utility — Upload → Preview → Commit. Not the default discovery path.

### 2.8 Login (only when `APP_ACCESS_TOKEN` is set)
- Standalone page (does not extend the app chrome — nothing to leak behind a gate): one password-style token field + Sign in, `next` carried as a hidden local path. Wrong token re-renders with an inline error (401); success sets the session, rotates the CSRF cookie and redirects to `next`. Every other page/API redirects here (HTML) or returns 401 (JSON) until signed in. `POST /logout` (CSRF-protected) ends the session.

## 3. Main user journeys (screen-level)

| Journey | Path |
|---|---|
| Morning triage | Dashboard → "Needs attention" → prospect/review/follow-up |
| New lead from phone call | Prospects → Add → (Research later) |
| Batch discovery day | Profile → run automated Discovery → review candidates → research queue |
| Research session | Prospect → Research tab → run → verify findings → Opportunities tab → validate |
| Send window | Outreach queue → pending_review → edit → approve → Open in WhatsApp → confirm sent → set follow-up |
| Weekly cleanup | Follow-ups overdue → complete/snooze → Settings → Usage → Backup |

## 4. Reusable components

| Component | Usage |
|---|---|
| `DataTable` | sortable, filterable, URL-state, row actions; used by prospects, outreach, follow-ups, usage |
| `FilterBar` | chips + selects + search; one implementation, URL-synced; removable active-filter chips (plain links) + mobile Filters drawer |
| `StatusBadge` | stage / message status / verification / origin — one vocabulary, consistent colors (see tokens) |
| `EvidenceChip` | source URL chip with favicon-less external-link icon + tooltip (type + retrieved date) |
| `PrimaryActionButton` | context-aware label (see outreach chain); never renders when state forbids (disabled + tooltip reason instead) |
| `EmptyState` | icon + one sentence + one action; used on every list/tab |
| `InlineEditField` | click-to-edit text/select with save/cancel, used across profile fields |
| `ConfirmDialog` | destructive/irreversible ops (erase, do-not-contact, approve) — text names the prospect |
| `Toast/Flash` | success/error after form posts; auto-dismiss, also via `HX-Trigger` |
| `TimelineItem` | activity entries |
| `WarningBanner` | suppression, possible duplicates, ungrounded drafts, check_failed status |
| `Stepper` | import wizard |
| `CopyButton` | phone/URL copy affordance |
| `WaLinkButton` | renders only when message `approved`; includes status tooltip |

## 5. Design tokens

Defined once in `app/static/css/app.css` `:root` (plain CSS custom properties; no framework). Pre-composited solid hex for every tint — never `rgba(…)` overlays of the semantic hues, or the contrast table stops holding:

```css
/* Surfaces — charcoal shade ladder, NO element outlines (2026-11-17)
   (body 14.52:1, muted 7.41:1 on surface; adjacent steps 1.11–1.21:1) */
--color-bg:#090a0c; --color-surface:#171a20; --color-surface-hover:#1f232a;
--color-surface-raised:#292d35;     /* menus, toasts, chips, active tiles */
--color-field:#0e1014;              /* sunken control fill: inputs, buttons */
--color-border:#33373f;             /* INTERNAL DIVIDERS only — never boxes */
--color-border-strong:#6a7280;      /* focus/error boundary ≥3:1 (WCAG 1.4.11) */
--shadow-1:0 1px 2px rgba(0,0,0,.4); --shadow-2:0 8px 24px rgba(0,0,0,.5);

/* Text — warm-tinted white (15.08 / 7.70 / 5.58:1 on surface) */
--color-text:#eceae6; --color-text-muted:#a6a9b0; --color-text-subtle:#8b8f97;

/* Single accent — primary actions, active nav, focus, running/hot, AI origin */
--color-accent:#ff6b1a; --color-accent-hover:#ff8340;   /* hover lightens on dark */
--color-accent-light:#3d271d;    /* accent tint — carries active states alone */
--color-accent-border:#7a3d1c;   /* pre-composited 35% orange over surface */
--glow-accent:0 0 20px rgba(255,107,26,.12);  /* CTA hover + active nav + focus rings */
--color-link:#ff8a3d;            /* same family, one hue lighter (7.72:1) */
--focus-ring:rgba(255,107,26,.28);

/* Semantic — tint bg + coloured text (word/icon always present, never colour
   alone; the *-border tokens remain for focused states only, not chip outlines) */
--color-success:#4cc38a; --color-success-light:#173b2b; --color-success-border:#1e5c3a;
--color-danger:#f87171;  --color-danger-light:#3a1d17;  --color-danger-border:#6e2f28;
                         --color-danger-hover:#4a211b;  /* destructive button hover */
--color-warn:#fbbf24;    --color-warn-light:#3a2f17;    --color-warn-border:#7a5a18;
--color-info:#56b8d8;    --color-info-light:#153440;    --color-info-border:#1d5563;

/* Type — system sans body + mono micro-typography; tabular numerals for data */
--font-sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
--font-mono: ui-monospace, "SFMono-Regular", "JetBrains Mono", Consolas, monospace;
--tracking-label:.08em;            /* uppercase mono group/column labels */
--fs-xs:11px; --fs-sm:13px; --fs-base:15px; --fs-lg:17px; --fs-xl:18px; --fs-3xl:28px;
--fw-medium:500; --fw-semibold:600;
--lh-tight:1.3; --lh-normal:1.5;

/* Space & shape — 4px base */
--sp-1:4px … --sp-8:32px;
--radius-sm:4px; --radius-md:6px; --radius-lg:8px;
--control-h:44px mobile / 36px ≥640px; --row-h:40px  /* row floor, rendered as td height (not a custom property) */;

/* Layout — grouped sidebar (spec §1 amendment 2026-11-11, §7) */
--nav-w:15rem; --topbar-h:52px; --max-w:960px;

/* Motion — functional only; fast/base for micro-interactions, slow for the
   drawer class; decelerating ease, never a snap (2026-11-17: "make it smooth
   and not too fast"). prefers-reduced-motion → 0ms / static. */
--dur-fast:120ms; --dur-base:200ms; --dur-slow:320ms;
--ease-out:cubic-bezier(0.22, 1, 0.36, 1);
```

**Amendment — borderless pass (2026-11-17, owner directives):**
1. **No element outlines.** Cards, zones, panels, drawers, badges, chips, buttons, inputs, banners, tooltips, menus and tables are fill-only; levels come from the shade ladder above. `--color-border` is dividers only (row rules, tab underline, region edges); `--color-border-strong` stays for focus rings and error borders. Focus outlines (`:focus-visible`, 2px accent) are untouched — they are state, not decoration.
2. **Stage tab.** The prospect card's stage pill became a **sunken corner tab** welded into the card's top-right edge: negative margins pull it onto the edge, its top-right radius matches the card's (`0 var(--radius-md) 0 var(--radius-sm)`), no border. Neutral fill by default; only an opt-out stage (`do not contact`) keeps the danger tint.
3. **Colour = signal.** On prospect cards every chip is neutral (`raised` fill, muted text); red survives for suppression/failure and amber for `check failed`/overdue. Semantic tints were brightened so chips still read as blocks without borders.
4. **Labels.** Card section headings that only restated their content (Key facts, Research, Opportunities, Outreach, Notes, Activity, Draft editor, Grounding, Send chain, Message history, Research status, Findings) are **removed**; headings remain only where they group many controls (Defaults, Daily caps, Backup, Discovery, Danger zone, Add finding). Panel titles are real `<h2>` so the outline never skips a level (axe `heading-order`).
5. **Panel arrangement.** The suppression banner sits *below* the sticky header; the head is one row (name + badges inline, actions opposite); danger-zone blocks carry their status in the block head instead of a `Status:` line; Outreach shows a state row + message preview; activity payloads render as readable text (`describe_payload`) — never JSON.

Typography: two families, strict role split — system sans for body/table/form text, mono for uppercase group labels, numeric readouts and log-style timestamps. Data tables use `font-variant-numeric: tabular-nums`. Hierarchy comes from weight/spacing/size, not color.

## 6. States (mandatory on every page/action)

- **Loading:** skeleton rows for tables; button spinners with label change ("Running research…"); HTMX `hx-indicator`.
- **Success:** toast + inline check; optimistic updates only for pure UI state.
- **Error:** inline field errors (422) and page-level banner (500) with request ID; retry where an action is retryable; provider failures show *what failed and what still succeeded* (partial results preserved).
- **Empty:** per §2 templates above — always with the next action.
- **Optimistic/partial:** research tab polls status partial; page never blocks on external providers.

## 7. Responsive behavior

| Breakpoint | Behavior |
|---|---|
| ≥1024px | Full canvas (transform layer); rail + wide tables (all columns) |
| 768–1023px | Rail + stacked flow as a **2-column zone grid** (mini-kanban); sticky zone heads; tables stack with `data-label` headers |
| <768px | Header + drawer; canvas in stacked **work-first order** (setup cluster last, DOM follows visual); toolbar = two rows with Add prospect as a 44px header icon; standalone pages get a sticky scrollable tab strip; tables → stacked rows *with data-label headers* (dense, not decorative) |

Touch targets **44px** on `pointer: coarse` and <768px (raised from 40: buttons
were 36px, dashboard list links 20px; inline prose links keep the WCAG inline
exception). Spacing rhythm on phone: 16px gutters, 12px between stacked zones,
24px between sections. The mobile experience is **review/light-triage quality**
(approve, confirm, follow-up); heavy CSV work is desktop-oriented but not blocked.

## 8. Accessibility

- Semantic HTML first (server-rendered helps): landmarks, real `<table>`, real `<button>`/`<a>`.
- Keyboard: visible focus rings (2px accent outline), full table navigation, `?` opens shortcut help (j/k row nav, `/` search, `g d` dashboard — progressive enhancement).
- Contrast: token pairs meet WCAG AA (muted text ≥4.5:1 on surface).
- Badges/icons always paired with text; status changes announced via `aria-live="polite"` on toasts.
- Forms: labels tied to inputs, errors with `aria-describedby`, focus moved to first invalid field.

## 9. Content & voice

- UI copy is plain and specific: "I have sent this message" not "Mark as sent"; "Could not verify website" not "Website missing".
- Dates: relative + absolute tooltip ("in 2 days · Nov 8, 2026").
- Empty states teach the workflow in one sentence.
- Never claim outcomes the system cannot know (no "delivered", no "read").

## 10. Explicit design exclusions

Gradients and glow backdrops (the accent glow is confined to the primary button hover, active nav, and focus rings), glassmorphism, animated counters, sparkline/donut/gauge decoration, HUD dot-matrix visuals, per-item colored nav markers (orange is the only non-neutral UI color), poster-scale display typography and mascot artwork, drag-and-drop Kanban (lists are faster to build, test, and keyboard-drive). "Colored sidebar" and "dark-mode-only styling" are rescinded by the 2026-11-11 visual redesign decision: the UI is dark-only and the sidebar carries a single accent.
