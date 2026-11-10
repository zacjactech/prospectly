# QA Checklist — living document (last full pass: 2026-11-10)

Full manual walkthrough of the user flows (USER_FLOWS.md). Each flow is
verified against the running app and backed by regression tests in `tests/`.
"Blocked" means a defect with no workaround; "not implemented" means a
spec surface is still pending (currently none — see *Known gaps*).

Run the app (Windows / project default):

```powershell
py -m uv run alembic upgrade head
py -m uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
py -m uv run python -m app.cli worker    # 2nd terminal — queued jobs only
process while this runs
```

Linux: `uv` is on `~/.local/bin` (prefix `export PATH="$HOME/.local/bin:$PATH"`);
same commands without the `py -m uv` prefix.

**Status legend:** `pass` = verified against running app or by a named
regression test · `pass*` = verified with fake/offline providers (real
provider key required for live data) · `not implemented` = spec gap.

---

## Flow 0 — Configure your profile (one-time setup)

| Step | Check | Status |
|------|-------|--------|
| Open Settings → Profile | page loads, form present | pass (2026-11-10) |
| Save offer / industries / locations / signals | values persist, flash shown | pass (2026-11-10) |
| Daily caps + providers shown read-only | values match config — **on Settings → General / Providers, not Profile** | pass |

## Flow 1 — Discover prospects automatically (default path)

| Step | Check | Status |
|------|-------|--------|
| Profile has target industries/locations | drives discovery queries (`ideal_signals` does **not** drive discovery) | pass (2026-11-10) |
| "Run discovery" button on Profile | `discovery_jobs` + `jobs` rows queued; flash names the worker command; 400 if profile has no targets | pass — `tests/test_discovery_and_views.py` |
| Worker processes the queue | `python -m app.cli worker --once` → job + discovery job `succeeded`; empty queue exits 0 | pass — `tests/test_worker.py`, e2e in `test_discovery_and_views.py` |
| Candidates appear in Prospects list | stage = new | pass* (offline: 0 real candidates; created via Fake* adapters on the real worker path) |
| Recent runs shown on Profile page | status / candidates / created / error table | pass — `test_discovery_and_views.py` |

## Flow 2 — Research a prospect (core evidence loop)

| Step | Check | Status |
|------|-------|--------|
| Click "Run research" | run + job created `queued`; `research_started` activity logged | pass — `test_worker.py` |
| Worker claims and runs it | run → `succeeded`/`partially_completed`; no job stuck `queued` | pass — worker e2e tests |
| Findings stored with source URL + origin | evidence retained (P7) | pass |
| Website status classified | `no_website_found` only from successful-empty search (+dns/no-url); timeout/blocked/failed-search → `check_failed` (P6) | pass — `tests/test_website_status.py` (17 tests) |
| Live progress | research page polls the HTMX partial every 2s while `running`; terminal fragment stops polling and refreshes findings in the same swap | pass — `tests/test_research_polling.py` |
| Stage advances new → researched | state machine enforced | pass |
| Second `worker --once` on empty queue | exits 0, no crash | pass (2026-11-10 QA) |

## Flow 3 — Add a prospect manually

| Step | Check | Status |
|------|-------|--------|
| "Add prospect" form loads | all fields present | pass |
| Create with phone | phone normalized to E.164 (D9) | pass |
| Duplicate name blocked | 409 with existing_id | pass |
| Flash "Prospect created" on success | toast shown | pass |

## Flow 4 — Identify and validate an opportunity

| Step | Check | Status |
|------|-------|--------|
| Research run completes | rule engine proposes opportunities | pass |
| Opportunity card shows summary + rationale + evidence chips | linked findings | pass |
| Validate | stage advances researched → opportunity_identified | pass |
| Validate blocked without evidence | 409, §2.8 guard | pass |
| Validate blocked on AI-only evidence | 409, P5 | pass |
| Dismiss | status = dismissed, stage unchanged | pass |
| No-URL prospect gets `no_standalone_website` | approved rule (no URL + successful empty search) | pass — `test_website_status.py` |
| Check-failed prospect does **not** get `website_inaccessible` from the "no URL" observation | evidence filter | pass — `test_website_status.py` |

## Flow 5 — Draft → approve → WhatsApp → confirm sent (the heart)

| Step | Check | Status |
|------|-------|--------|
| Generate draft (twice) | second draft deactivates the first — partial unique index, no 500 | pass — `test_regressions_round4.py` (QA D1) |
| Edit text | demotes to draft (§3.1) | pass |
| Submit for review | draft → pending_review | pass |
| Approve blocked without grounding / ack | 409 | pass |
| Approve with acknowledgement | draft → approved | pass |
| Open in WhatsApp | wa.me URL built, never sent (P4) | pass |
| Confirm sent | `sent_confirmed` + activity + `last_contacted_at` + **stage `contacted`** from any active pre-contact stage | pass — `test_suppression_and_stages.py` |
| Confirm sent on `follow_up_due` | stage stays `follow_up_due` (re-contact must not clear it) | pass — `test_suppression_and_stages.py` |
| Not sent | opened_in_whatsapp → approved, logs `message_not_sent` (not `draft_approved`) | pass — `test_suppression_and_stages.py` |
| Edit a `sent_confirmed` message | **rejected 409** — terminal state, history preserved (M7) | pass — `test_suppression_and_stages.py` |
| Start new outreach after sent_confirmed | creates the next active draft (Flow 7 guard) | pass — `test_regressions_round4.py` |

## Flow 6 — Reply handling

| Step | Check | Status |
|------|-------|--------|
| Record reply — UI form on prospect detail ("Record reply" card) | 303, activity `reply_received` | pass — `test_regressions_round4.py` (QA D4) |
| Record reply — API `POST /api/v1/prospects/{id}/replies` | 200 + stage `replied`; empty text 400; suppressed 403 | pass — `test_regressions_round4.py` (matches API_DESIGN §2.6) |
| Stage → replied from lagging stages | opportunity_identified / contacted / follow_up_due all land `replied` | pass — `test_suppression_and_stages.py` |
| Reply never overwrites a terminal disposition | `not_interested` stays | pass — `test_suppression_and_stages.py` |
| Interested / Not interested | stage transitions enforced; `not_interested` offered from every active stage and is terminal for outreach (draft → 409) | pass — `test_suppression_and_stages.py` |
| Do not contact | via suppression card (Flow 8), not the stage dropdown | pass |

## Flow 7 — Follow-up

| Step | Check | Status |
|------|-------|--------|
| Add follow-up with due date | row created, status open (on prospect detail — there is no standalone Follow-ups page) | pass |
| Due today counts as "due" | dashboard metric | pass |
| Overdue (due_date < today) | dashboard metric, timezone-tested (UTC) | pass |
| Complete | one `follow_up_completed` activity; second attempt **409**, no re-stamp of `completed_at` | pass — `test_discovery_and_views.py` (M4) |
| Cancel | one `follow_up_cancelled` activity on **both** surfaces; repeat 409 | pass — `test_discovery_and_views.py` |
| Start new outreach after confirm-sent | works (partial index) | pass — `test_regressions_round4.py` |

## Flow 8 — Opt-out / do not contact

| Step | Check | Status |
|------|-------|--------|
| Set suppression — detail page "Do not contact" card (reason optional) | flag + stage `do_not_contact` + `do_not_contact_set` activity carrying the pre-suppression stage | pass — `test_suppression_and_stages.py` |
| Set suppression — API `POST /api/v1/prospects/{id}/do-not-contact` | 200 | pass |
| Stage dropdown/API → `do_not_contact` | sets the **flag** too (guards read the flag — no stage-only ghost suppression) | pass — `test_suppression_and_stages.py` |
| Draft / approve / open blocked while suppressed | 403 on all outbound paths | pass |
| Clear — required reason | empty reason → **400** (HTML + API); whitespace → 400 | pass — `test_regressions_round4.py` (QA D3) |
| Clear restores the pipeline | flag off + stage reverted to pre-suppression stage + `do_not_contact_cleared` activity | pass — `test_suppression_and_stages.py` |
| Merge propagates opt-out | suppressed source merged into live target → target suppressed with the original reason; clear restores the target's stage | pass — `test_regressions_round4.py` (N3) |

## Flow 9 — Conversion

| Step | Check | Status |
|------|-------|--------|
| Set stage converted | prospect leaves active counts | pass |
| Handoff note recorded | Notes card on prospect detail: add/edit, `note_added`/`note_edited` activities | pass — `tests/test_notes.py` |

## Flow 10 — CSV import (secondary Settings utility)

| Step | Check | Status |
|------|-------|--------|
| Fresh-browser upload (no cookie yet) | rendered token == cookie; multipart POST passes CSRF (C2) | pass — `test_csv_import.py` form-replay e2e |
| Form actions post to real routes | `/settings/import` (+ `/commit`) | pass — template replay test |
| E.164 `+49…` phone imports | valid row, not a formula-injection error (M8) | pass — `test_csv_import.py` |
| Invalid CSV (no name column) | 400 with message, not 500 (C2) | pass — `test_csv_import.py` |
| API preview with an error row | 200 (flatten no longer 500s) | pass — `test_csv_import.py` |
| Commit selected rows | created / skipped_duplicates / errors all counted (no silent zeros) | pass — `test_csv_import.py` |
| Session files | 24-hex token only (no traversal), 24h TTL, swept on save (M5) | pass — `test_csv_import.py` |

## Flow 11 — Review & correction day (maintenance loop)

| Step | Check | Status |
|------|-------|--------|
| Dashboard "Needs attention" queue | overdue → pending review → failed research → unverified findings | pass (metrics match DB, 2026-11-10 QA) |
| Fix unverified findings | Verify / Edit / Delete on the research page + findings API (origin preserved, AI audit, linked-delete 409) | pass — `tests/test_findings_crud.py` |
| Approve pending drafts | Outreach queue | pass |
| Clear overdue follow-ups | follow-ups on prospect detail | pass |
| Settings → Usage | provider errors visible; daily buckets don't crash (period DATE, H5) | pass — `test_website_status.py` |
| Backup | snapshot written to backups/ | pass (2026-11-10 QA) |
| Retention sweep | `months` clamped 1..120 (months=0 flags only genuinely inactive), `skipped` truthful | pass — `tests/test_merge_retention.py` (M2) |
| Merge duplicates page | renders; identical findings don't crash the merge (N1); single active message survives | pass — `test_regressions_round4.py` |
| Saved views: save current → apply | filters whitelisted; apply redirects onto the filtered list; unknown JSON keys → 400 | pass — `test_discovery_and_views.py` (M3) |

---

## Dashboard metrics (PRODUCT_BRIEF §8)

| Metric | Definition | Status |
|--------|------------|--------|
| Total prospects | non-archived count | pass |
| Researched | ≥1 succeeded research run | pass (note: `partially_completed` runs excluded by design) |
| Requires review | unverified/contradicted findings or pending drafts | pass |
| Approved messages | status = approved | pass |
| Sent (confirmed) | status = sent_confirmed | pass |
| Replies | ≥1 reply_received activity | pass |
| Interested | stage = interested | pass |
| Follow-ups due | open, due_date ≤ today | pass |
| Converted | stage = converted | pass |

---

## Guards (enforced in code)

| Guard | Status |
|-------|--------|
| No auto-send / bulk WhatsApp | pass — grep-verified 2026-11-10: only `build_wa_me_url`; no SDK/Graph/browser-automation/send endpoint anywhere |
| Human approval before outbound | pass (state machine, confirm-sent sole writer) |
| Suppression blocks all outbound | pass — draft / generate / start-new-outreach / approve / open / reply-recording all 403 while flagged; `wa.me` link only rendered after the service guard |
| No invented facts | pass (AI origin = unverified, low confidence; C4 regression test) |
| "No website" ≠ "could not verify" | pass — P6 matrix in `test_website_status.py` |
| No duplicates | pass (partial unique indexes) |
| Illegal stage / message transitions | 409 |
| `sent_confirmed` terminal | pass — edits rejected 409 |
| Access gate (when `APP_ACCESS_TOKEN` set) | pass — 401 JSON / 302 to `/login`, allowlist `/login` + `/healthz` + `/static/*` only; CSRF on login; 12h sliding session (`tests/test_auth.py`, 36 tests) |

---

## Test suite

`uv run pytest` — **291 tests pass, 0 failed** (2026-11-10); `ruff check`,
`ruff format --check`, `mypy app` all clean.

Coverage by area (newest first):
- `test_findings_crud.py` — 18: forced provenance (P5), duplicate/collision
  409s, verify idempotency, AI first-edit audit, linked-delete guard, HTML
  + API parity incl. list filters
- `test_notes.py` — 10: add/edit + activities, validation, both HTML routes
- `test_research_polling.py` — 6: running→polling attrs, terminal→attrs
  dropped (self-stopping), findings inside the fragment, htmx vendored
- `test_regressions_round4.py` — 13: partial message index (D1), template
  compile sweep + the three former 500 pages (D2), clear-reason 400 (D3),
  reply form + API (D4), merge identical-findings (N1), merge suppression
  propagation (N3), worker claim resilience (N2), **no inline event
  handlers in any template** (CSP — found by the browser pass)
- `test_auth.py` — 36: login gate, session cookie, redirects, rotation
- `test_discovery_and_views.py` — 11: discovery trigger + worker e2e,
  saved views save/apply/whitelist, follow-up idempotency both surfaces
- `test_suppression_and_stages.py` — 12: suppression round trips both
  surfaces, flag/stage sync, event stage edges, terminal sent_confirmed
- `test_csrf.py` — 7: first-visit token==cookie, multipart accept/reject,
  non-ASCII fails closed
- `test_csv_import.py` — 11: browser-form replay e2e, error paths,
  session TTL/traversal, commit accounting, E.164 phone
- `test_worker.py` — 9: claim/result commit boundaries, FK-safe reclaim,
  backoff, once-mode
- `test_website_status.py` — 17: P6 derivation matrix, P5 no-URL finding,
  opportunity misfire guard, usage upsert/period
- `test_merge_retention.py` — 9 · `test_safe_fetcher_redirects.py` — 6
  (per-hop SSRF revalidation) · `test_smoke.py` — 4 (hermetic schema)
- Earlier phases: `test_dashboard_metrics` (11), `test_phase3_routes`,
  `test_whatsapp` (38), `test_opportunity` (9), `test_outreach` (9),
  `test_phase5_security` (12), `test_phase5_failure_injection` (6),
  `test_phase5_performance` (3), `test_bizdata_provider` (6), `test_phase1`,
  `test_phase5_*`

## Phase 6+ Free Features

| Feature | Description | Status |
|---|---|---|
| Saved Views | Save current list filters; Apply from Settings (whitelisted filters) | ✅ Implemented + tested (2026-11-10) |
| Fuzzy Merge UI | Prefiltered duplicate review + merge (findings collision-safe, suppression propagated) | ✅ Implemented + tested (2026-11-10) |
| Retention Sweep | Clamp 1..120 months, truthful `skipped`, clear flag | ✅ Implemented + tested (2026-11-10) |
| Access gate | `APP_ACCESS_TOKEN` login gate with signed sliding sessions | ✅ Implemented + tested (2026-11-10) |

## Manual QA walkthrough — 2026-11-10 (post-fix re-verification)

56 automated checks over HTTP against an isolated DB (`/tmp/opencode/qa2`),
plus a full `pytest` run: **flows 0–7, 9, 10 pass**; flows 8 and 11 failed
on defects D1 (message index), D2 (three 500 pages), D3 (clear-reason 422)
— **all three fixed in the same pass and covered by regression tests**
(`test_regressions_round4.py`). Reviewer verification of the fix series
(a596f8d..921437b): all 18 original findings RESOLVED; new findings N1–N4
fixed or documented. Invariant grep re-confirmed: no WhatsApp send path.

### Browser verification pass — 2026-11-10 (Playwright, headless Chromium)

First real-browser pass over the previously cookie-jar-only behaviors
(isolated instance on `127.0.0.1:8177`, seeded `running` research run):

- **HTMX polling**: `hx-get` armed only while `running`; measured cadence
  exactly every 2s (single healthy timer — no accumulation across swaps);
  after the run flipped to terminal the attrs dropped, findings refreshed in
  the same swap, run/re-run buttons rendered inside the polled region, and
  the poll counter **froze** (91 → 91 over 5 quiet seconds) — self-stopping
  confirmed in a live browser.
- **Confirm dialogs**: initial-load `data-confirm` (Archive) → dialog shown,
  zero POSTs, state unchanged; **HTMX-swapped** `data-confirm` (Delete
  finding injected by a poll) → dialog shown, zero POSTs, finding intact —
  the capture-phase delegation fix verified on swapped content.
- **Console: 0 errors** after three fixes the pass uncovered:
  1. `base.html` shipped an inline `onclick` the CSP silently blocked
     (help backdrop was dead) → `data-hide` attribute + app.js handler;
  2. the help **Close button** used `data-dismiss`, which adds `.dismiss` —
     a class only `.toast` styles — so it was a no-op too (only `Escape`
     ever closed help) → same `data-hide` fix, browser-re-verified both paths;
  3. htmx's injected `.htmx-indicator` `<style>` was blocked by
     `style-src 'self'` → its exact sha256 (browser-verified byte-for-byte
     against the blocked content) added to CSP in `app/main.py`; favicon
     404 silenced with `<link rel="icon" href="data:,">`.
- Help overlay: `?` opens; Close button and backdrop both close (they
  didn't before this pass).
- Regression tests added: CSP carries the hash and never `unsafe-inline`
  (`test_smoke.py`); no template may contain an inline event handler
  (`test_regressions_round4.py`).

## Live smoke — Phase 1, keyless — 2026-11-10

Real network, isolated instance (`/tmp/opencode/live-smoke`), no API keys:
DDGS search + OSM discovery + template AI. Worker CLI processed every job.

- **Flow 1 (discovery)**: profile → Run discovery → worker → **5 real
  candidates** (real names/URLs from live DuckDuckGo) created as `new`;
  job `succeeded`, attempts=1. OSM path: `overpass-api.de` returns **406 at
  the nginx edge for every request from this host** (v4 and v6, even a
  one-node query) — environment block, not an app defect; the run now
  honestly reports `partially_completed` with the real error instead of the
  old silent "succeeded, 0 candidates".
- **Flow 2 (research, three classification cases)**:
  - `python.org` → HTTP 200 → `website_status=reachable` + "Website
    reachable" finding with source URL;
  - `hyatt.com` → HTTP 403 (bot wall) → `check_failed` ("could not
    verify", never "no website") — **C3 in anger**;
  - `*.example` NXDOMAIN → `dns_failure`, and because the live search
    returned results → `check_failed` (C3: dns_failure alone is not
    enough when search found sites) — partial run captured the real
    `Errno -2` message.
- **Flow 5**: template draft (grounded) → submit → approve → open →
  **wa.me URL with the exact E.164 phone** → not-sent → approved,
  `opened_at` cleared. No message sent (P2/P4).
- **H5**: one `provider_usage` row per provider/day with accumulated
  counts (`http_fetch/inspect calls=2`, `ddgs/search calls=2`) — the
  second-same-day upsert that used to crash now merges.
- **Invariants on the live DB**: 0 AI-origin findings (all provider,
  `unverified`), no WhatsApp send path in `app/`, all jobs terminal,
  idle worker exits 0.

### Findings found & fixed in the live loop (commit below)

1. **Every provider swallowed HTTP errors and returned `[]`** — a failed
   search was indistinguishable from "no results" and could feed P6's
   `no_website_found` with a failed search. ddgs/tavily/serper/osm now
   **raise**; bizdata raises only when *every* fan-out call failed
   (partial failure still degrades gracefully). Live-proven: the Overpass
   406 surfaced as `partially_completed` with the real error.
2. **OSM query was structurally dead for its flagship case**: it only
   matched `shop=*` (hotels are `tourism=*`) and ignored locations
   entirely (the area-scoped query was dead code, overwritten). Now:
   named-area scoping (`area["name"="…"]`, no Nominatim needed) ×
   shop/amenity/tourism × node/way, with profile strings escaped.
   Unit-verified (`test_search_discovery_providers.py`, 9 tests);
   live re-test deferred — Overpass blocks this host (see above).

### Not verified in this pass (honest limits)

- Live external providers: **DDGS search + real website inspection
  verified live (2026-11-10)**; Tavily/Serper/AI providers still
  offline-only (Phase 2, keyed); OSM unreachable from this host
  (406 at edge — re-test from an unblocked network).
- Backup **restore** and `alembic downgrade` (upgrade path tested).
- Non-loopback bind with token (startup refusal tested; live LAN exposure
  not exercised) — `Secure` cookie flag is off by design for loopback.
- Login rate limiting: none (documented in SECURITY §3).

## Known gaps (spec promises not yet built)

**None as of 2026-11-10.** The last three — findings evidence CRUD, the
notes surface (Flow 9), and HTMX research-progress polling — shipped in
this pass and are covered by `test_findings_crud.py`, `test_notes.py` and
`test_research_polling.py`. (The `POST /prospects/{id}/findings` and
`/research/status` rows in API_DESIGN are now live, not "not implemented".)

## Phase 5 — Hardening & Release (complete)

- Security pass: all §9 abuse-case items tested and passing
- Failure-injection: provider 429/500/timeout → partially_completed, partial results preserved
- Performance: 5,000-prospect list <200ms, indexes verified via EXPLAIN QUERY PLAN
- Docs consistency: BizData added to SYSTEM_ARCHITECTURE diagram, DATABASE_DESIGN source_type enum, RESEARCH_FINDINGS table, COST_ANALYSIS table
- Tag `v0.1.0`: exists in git
