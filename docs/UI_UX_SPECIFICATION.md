# UI/UX Specification

Design direction: **professional operations tool** — information-dense, quiet, and legible for repeated daily use. Explicitly *not* the "AI dashboard" aesthetic: no gradient heroes, no floating card grids, no decorative donut charts, no color-coded-everything, no animation for its own sake.

Influences researched (2026): CRM UX best-practice literature (aufaitux, eseospace, designstudiouiux) emphasizing density + clarity over visual flourish; practitioner HTMX-vs-React write-ups confirming server-rendered CRUD patterns; Linear/Notion-style restraint (compact tables, keyboard-friendly filters, inline editing).

---

## 1. Information architecture & navigation

**Amendment (2026-11-07):** the MVP ships a **persistent top bar**, not the left sidebar below. Rationale: with only Dashboard / Prospects / Profile there are 3 sections, and research on navigation patterns (NN/g) says a sidebar only pays for itself past ~8 sections; the top bar also keeps the table full-width for density. The sidebar returns when Follow-ups, Outreach and Settings land (Phase 4+). The IA tree below is the target state; the shipped nav shows the three items in bold.

```
Prospectly
├── Dashboard            (pipeline strip + "needs attention" queue)
├── Prospects            (list = home base; filters, saved views)
│     └── Prospect detail (tabs: Overview · Research · Opportunities · Outreach · Activity)
├── Follow-ups           (due / overdue / upcoming)
├── Outreach             (message queue by status: pending_review → approved → opened_in_whatsapp)
├── Settings
│     ├── Profile        (offer, target industries/categories, locations, signals — drives discovery + drafts)
│     ├── Import         (CSV utility — secondary, not part of discovery flow)
│     ├── Providers      (keys/status, feature flags)
│     ├── Usage          (provider + AI usage, errors, rate limits)
│     └── General        (vocabulary, defaults, backup)
└── Docs (link to /docs)
```

**Design rule:** the pipeline is never more than two clicks deep; every list page has one dominant primary action in the header (Add prospect / Import / Run research / Generate draft).

**Design system:** `app/static/css/app.css` is the single source of visual truth — flat surfaces with 1px hairline borders (no shadow stacks), near-black primary buttons, blue reserved for links/focus, compact 13px table rows, semantic badges only for status. Behaviors live in `app/static/js/app.js` (CSP forbids inline handlers): mobile nav toggle, `data-confirm` prompts, whole-row navigation.

## 2. Pages & responsibilities

### 2.1 Dashboard
- **Responsibility:** answer "what do I do next today?" — not analytics theater.
- **Sections:**
  1. *Needs attention* queue (overdue follow-ups, drafts pending review, research runs failed, findings unverified) — the primary block, sorted by urgency. Empty state: "Nothing needs attention. ✓"
  2. *Pipeline strip*: counts for every stage, grouped into three labelled display phases so the numbers read as a progression — **Qualify** (new, researched, opportunity, ready) · **Outreach** (contacted, replied, follow-up) · **Convert** (interested, converted). Each phase is its own bordered card of stage cells (not a funnel chart, not a single undifferentiated row). Phase grouping is presentation-only: stage order and semantics come from `SYSTEM_ARCHITECTURE.md` §3.2 and are unchanged.
  3. *Today*: follow-ups due today + recently replied.
  4. *Metrics groups*: the nine operational metrics (`PRODUCT_BRIEF §8`) arranged as **three themed cards** — **Pipeline** (Total, Researched, Requires review) · **Outreach** (Approved, Sent, Replies) · **Results** (Interested, Follow-ups due, Converted). Each card is a bordered surface with an uppercase group label and left-label / right-value rows (tabular numerals). Empty values are dimmed; actionable backlogs (Requires review > 0, Follow-ups due > 0) read in `warn`. Cards sit in one column on mobile, three columns from 640px. Grouping is presentation-only: the metric definitions come from `app/services/dashboard.py` and are unchanged.
- All numbers link to the pre-filtered Prospects list.

**Status (Phase 5):** the **pipeline strip** and all **nine metrics groups** are built and rendered honestly — real per-stage counts of non-archived prospects (`app/services/dashboard.py`, whose `PIPELINE_PHASES` defines the stage grouping and derives `PIPELINE_STAGES`), zero-filled and grayed when empty, every pipeline cell linking to `/prospects?stage=…`. The "Needs attention" queue is live. Empty dashboard: one sentence + Add prospect / Set up profile.

### 2.2 Prospects list (home base)
- **Responsibility:** scan, filter, triage.
- Dense table (rows ~40px): Name · Category · Location · Website status · Stage · Last contact · Next follow-up · Flags (duplicate? suppressed? unverified?).
  **Status (Phase 2):** Next follow-up and the "requires review" flag are Phase 4 columns and are not rendered yet (showing a permanent "none" column would be a lie); the rest are live. Website status and stage badges use `stage-<value>` color mappings, where `no_website_found` reads as neutral **info**, never as an error (P6).
- Filter bar: stage, category, tag, location, "requires review", archived toggle, free-text search. Filter state persists in URL (shareable/back-button safe). When any filter is active, an inline **Save current view** form (name + save) appears beside the bar; saved views apply from Settings → Saved views (`GET /prospects/views/{id}/apply` redirects onto the list with the view's whitelisted filters).
- Row click → detail. Hover reveals quick actions (Run research, Open WhatsApp — only when state allows).
- Bulk selection limited to safe ops: add tag, archive, change source. **No bulk outreach actions exist anywhere** (P1).
- Empty state: "No prospects yet — configure your Profile, run automated Discovery, or Add manually" + two buttons.

### 2.3 Prospect detail
- **Header:** name, stage badge, key facts (phone with copy, email, website with "open" + status chip), suppression warning banner if `do_not_contact`.
- **Stage card:** allowed-next-transitions select only (canonical steps + `not_interested` from post-contact stages); anything else is rejected server-side with 409.
- **Suppression card:** when not suppressed — "Do not contact" button with optional reason input (confirm dialog names the prospect); when suppressed — reason + last-suppression context and a "Clear suppression" form with a **required** reason (Flow 8: clearing is explicit and audit-logged).
- **Tab: Overview** — profile fields (inline edit), source, tags, notes (add/edit), next follow-up, primary actions (Run research / Generate draft / stage transitions).
- **Tab: Research** — findings grouped by kind; each finding row: statement · origin badge (`human`/`provider`/`ai`) · verification badge · confidence · source link (opens in new tab) · retrieved date · actions (Verify/Edit/Delete). Website check history sub-panel with outcome + signals. "Add finding" form.
- **Tab: Opportunities** — cards *only here* (each is an evidence object): type, summary, rationale, linked evidence chips (click → jump to finding), Validate/Dismiss. Empty state explains the rule engine runs after research.
- **Tab: Outreach** — message versions list (status timeline), current draft editor with character/line guidance, grounding panel (findings used), the gated action chain as **one primary button whose label reflects the next legal step**: `Submit for review → Approve → Open in WhatsApp → I have sent this message`. Secondary: edit, return to draft, start new outreach (guarded). After open: an explicit confirmation prompt "Did you send it?" with Yes/No — No returns to `approved`. AI-generated drafts carry an `ai` origin badge plus "verify every claim before sending" (P5); template drafts show `template`.
- **Tab: Activity** — append-only timeline (icon, actor, description, timestamp; payload details expandable).

### 2.4 Outreach queue
- Kanban-free **grouped list** by message status: `pending_review` (needs me) → `approved` (ready to send) → `opened_in_whatsapp` (did I send?). Each row: prospect, first line of message, age, actions (Review / Open / Confirm sent). Purpose: never lose an approved-but-unconfirmed message.

### 2.5 Follow-ups
- Grouped: **Overdue** (red count) · **Today** · **Upcoming**. Rows: prospect, due date, note, last contact. Actions: Complete, Snooze, Open prospect. Overdue group uses restrained amber/red only on the count chip — no row backgrounds.

### 2.6 Import (CSV — secondary utility)

Moved under Settings because CSV is not the default discovery path. 3-step wizard: Upload → Preview (valid/duplicate/error sections with per-row reasons, row checkboxes) → Commit + result summary. Shows backup reminder before commit.

### 2.7 Settings
- **Profile:** your offer, target industries/categories, target locations, ideal-client signals — one editable form; drives automated discovery and personalizes drafts. Below it: **Automated discovery** card — a "Run discovery" button (queues a `discovery_job` + worker job; flash names the worker command) and a recent-runs table (status, candidates found, created, error).
- **Saved views:** list of named list-filters with an **Apply** link (redirects to the filtered list) and delete; create form accepts structured list filters (unknown keys rejected with 400).
- **Providers:** per provider: configured? (key present), enabled toggle, "Test" button (runs a trivial call, shows result), last error. Keys themselves are never displayed — only masked presence (`GEMINI_API_KEY · configured`).
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
| `FilterBar` | chips + selects + search; one implementation, URL-synced |
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

Defined once in `static/css/tokens.css` (plain CSS custom properties; no framework):

```css
/* Color — neutral, single accent */
--bg:#fafafa; --surface:#ffffff; --border:#e4e4e7; --text:#18181b; --text-muted:#71717a;
--accent:#1d4ed8;        /* actions, links — one accent only */
--danger:#b91c1c; --success:#15803d; --warn:#b45309;
/* Stage/status badges: neutral bg + colored text; color never the only signal (icon/text always present) */

/* Type — system stack, tabular numerals for data */
--font: ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
--fs-xs:.75rem; --fs-sm:.8125rem; --fs-base:.875rem; --fs-lg:1rem; --fs-xl:1.25rem;
--fw-medium:500; --fw-semibold:600;
--lh-tight:1.25; --lh-normal:1.5;

/* Space — 4px base */
--sp-1:4px … --sp-6:24px;  --row-h:40px;  --control-h:32px;

/* Motion — functional only, ≤150ms, respects prefers-reduced-motion */
--dur:120ms;
```

Typography: one family, three sizes, two weights — hierarchy comes from weight/spacing, not color. Data tables use `font-variant-numeric: tabular-nums`.

## 6. States (mandatory on every page/action)

- **Loading:** skeleton rows for tables; button spinners with label change ("Running research…"); HTMX `hx-indicator`.
- **Success:** toast + inline check; optimistic updates only for pure UI state.
- **Error:** inline field errors (422) and page-level banner (500) with request ID; retry where an action is retryable; provider failures show *what failed and what still succeeded* (partial results preserved).
- **Empty:** per §2 templates above — always with the next action.
- **Optimistic/partial:** research tab polls status partial; page never blocks on external providers.

## 7. Responsive behavior

| Breakpoint | Behavior |
|---|---|
| ≥1200px | Sidebar + wide tables (all columns) |
| 768–1199px | Sidebar collapses to icons; tables drop low-priority columns into row expand |
| <768px | Top hamburger nav; tables → stacked cards *of rows* (still dense, not decorative); prospect detail tabs scroll horizontally; primary actions become sticky bottom bar |

Touch targets ≥40px on mobile. The mobile experience is **review/light-triage quality** (approve, confirm, follow-up); heavy CSV work is desktop-oriented but not blocked.

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

Gradients, glassmorphism, animated counters, sparkline/donut decoration, colored sidebar, dark-mode-only styling (system `prefers-color-scheme` support is a post-MVP nicety), drag-and-drop Kanban (lists are faster to build, test, and keyboard-drive).
