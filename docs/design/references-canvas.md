# Design research references — Pipeline Canvas

Grounding for the "Pipeline Canvas" redesign (ui-engineering-standards §2).
All pages fetched **2026-11-12**. Note: Canva's Help Center pages
(www.canva.com/help/page-view-settings/, /help/tools-tab/) were unreachable —
bot/browser gate returned an "update your browser" wall instead of content —
so reference 1 substitutes **Figma**, an equivalent live canvas tool, as
permitted by the brief.

## 1. Visual workspace / canvas — Figma Design editor shell

Sources (all retrieved 2026-11-12):
- *Explore the navigation bar and left sidebar* — https://help.figma.com/hc/en-us/articles/360039831974
- *Adjust your zoom and view options* — https://help.figma.com/hc/en-us/articles/360041065034
- *Use Figma products with a keyboard* — https://help.figma.com/hc/en-us/articles/360040328653

**What to take**
1. **Three-zone shell:** left-most vertical *navigation bar* (icon tabs for essential workflows, with hotkeys Alt/1, Alt/2), a *dynamic left sidebar* whose content changes per tab and is user-resizable, then the canvas — exactly our icon-rail → slide-over → canvas layering.
2. **Panels yield to the work surface:** `⌘⇧\` minimizes nav + sidebars; when an object is selected the right panel re-expands, and collapses again on deselect. Contextual panels that appear/disappear around selection, not permanently.
3. **Zoom as a first-class, hotkeyed system:** default is *zoom to fit*; `Shift+1` fit, `Shift+2` zoom-to-selection, `Shift +/-`, arrow-key pan with Shift to accelerate, `⌘ +/-` — every zoom state has a keyboard path and a visible percentage readout.
4. **Keyboard parity is explicit and default-on** ("keyboard controls are enabled by default and cannot be disabled"), including a keyboard box-selection cursor and a searchable shortcuts panel (`Ctrl+Shift+?`) that updates as you use shortcuts.

**What NOT to copy:** multiplayer cursors, plugins/widgets/shaders marketplace, and the "Agents" chat tab — none exist in a local-first single-user tool and each would violate CSP/no-build constraints if built as mini-apps. Figma itself concedes some actions (connectors, multi-click objects) are mouse-only — we must not repeat that gap for stage moves.

**Audience fit:** designers, often collaborative; overlaps with us on expert, keyboard-driven daily use, but not on multi-user or extensibility. Take the shell and zoom grammar, not the platform.

## 2. CRM / pipeline stage board — Attio kanban views

Source (retrieved 2026-11-12): *Create and manage kanban views* — https://attio.com/help/reference/managing-your-data/views/create-and-manage-kanban-views

**What to take**
1. **Cards carry only what's actionable:** hover the record name for *Edit entry* / *Open preview* (details open **without leaving the board** — our slide-over justification); bottom edge holds note/task/comment quick actions.
2. **Density is user-controlled per view:** choose displayed attributes, toggle *Show attribute label*, *Hide empty attributes*, plus **collapse columns** (shows only stage color, name, record count) and **collapse cards** (names only) — three density gears for the same board.
3. **Stage hygiene features with a job:** hide/collapse stages, per-column totals, instant calculations at the bottom of each stage, and *Track time in stage* where an expired target turns the card's counter red — a triage signal, not decoration.
4. **Bulk triage:** checkbox multi-select + shift-range selection, then one drag moves the batch — matches our "daily triage" verb.
5. Filters and sort live in the view chrome (Filter / Sort buttons), not on the canvas itself.

**What NOT to copy:** *Confetti stages* (celebration gimmick — P12 spirit, no job in expert triage), and the fact that this doc describes **only mouse paths** for moving cards (no keyboard move documented) — we need `S`/command-menu equivalents as Linear does. Also skip relationship-attribute fan-out and edit-history upsells: multi-seat CRM features.

**Audience fit:** sales teams in a multi-seat CRM; matches our pipeline-triage core but not our solo, non-CRM breadth (outreach verification, message review).

## 3. Dark, information-dense ops tool — Linear

Sources (all retrieved 2026-11-12):
- *Inbox* — https://linear.app/docs/inbox.md
- *Display options* — https://linear.app/docs/display-options.md
- *Board layout* — https://linear.app/docs/board-layout.md
- *Keyboard shortcuts help* (changelog) — https://linear.app/changelog/2021-03-25-keyboard-shortcuts-help

**What to take**
1. **Dark-by-default restraint:** docs screenshots show a dark UI where color is carried by status/priority only; surfaces stay neutral — validate our charcoal + single orange accent against this (one accent, status colors earn their place).
2. **Density is a setting, not an aesthetic:** Display options pick which properties render on list rows or board cards (`Shift+V`), grouping/ordering switch with `Cmd+B` list↔board; group headers stay **sticky** while scrolling.
3. **Cards are deliberately partial:** "Descriptions are not shown on cards… not all properties may have space" — press `Space` while hovering to **peek** for detail, or open the issue. Progressive disclosure instead of fat cards.
4. **Keyboard-first everywhere:** `G` `I` navigation chords, `J`/`K` list motion, `X`/`Shift+X` multi-select, `Alt+Shift+↑/↓` to move a card to top/bottom of a column, `S`/command menu to change stage (lands at column top), `T` collapse swimlane, `?` opens a searchable shortcuts help. Drag exists; keyboard parity always outranks it.
5. **Boards and lists share one behavior model** ("functionality and keyboard shortcuts are almost exactly the same") — one mental model, two layouts.

**What NOT to copy:** swimlanes/sub-grouping and workspace-shared manual ordering (multi-user concerns), the notification/Inbox surface (our app has one user and no social signals), and marketing-heavy feature naming. Its React-app implementation model is irrelevant to us — extract principles only.

**Audience fit:** the strongest match — software teams doing daily issue triage, dense and keyboard-first like our expert user.

## Category clichés to avoid (canvas/board tools)

- Infinite-canvas novelty with no job: our board is bounded stage zones, not free-form space to explore.
- Drag-only stage changes — every move needs a keyboard/command-menu path (Linear's `S` rule).
- Decorative minimaps, zoom toys, or "zoom to fit" buttons that hide a missing focus model.
- Kanban color rainbows: per-stage hues as the primary layout signal instead of position/labels.
- Fat cards showing every field; use peek/preview + hide-empty discipline instead.
- Card-in-card nesting and rounded-pill proliferation inside stage zones.
- Celebration feedback (confetti, streaks) in a serious daily-ops tool.
- Marketing-surface copy and onboarding garnish inside the work shell.
- Panels that permanently occupy canvas width instead of contextual slide-overs.
- Status conveyed by color alone (WCAG; color must pair with text/icon).

## Unknowns

- Canva specifics unverified (page unreachable); Figma used as substitute.
- Linear's exact theme tokens/dark hex values not published in these docs — contrast must be computed from our own tokens, not estimated from screenshots.
- Attio keyboard-equivalent stage moves: none documented on the fetched page; assumed absent, not confirmed absent.
