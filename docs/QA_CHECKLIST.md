# QA Checklist — living document (last full manual pass: 2026-11-11; canvas contract pass: 2026-11-16)

Full manual walkthrough of the user flows (USER_FLOWS.md). Each flow is
verified against the running app and backed by regression tests in `tests/`.
"Blocked" means a defect with no workaround; "not implemented" means a
spec surface is still pending (two are explicitly deferred — see *Known
gaps*).

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

`uv run pytest` — **378 tests pass, 0 failed** (2026-11-17); `ruff check`, `ruff format --check`, `mypy app` all clean. The 378 include the
45-test canvas contract suite below (333 baseline + 45). Dependency audit
(`uvx pip-audit`) reported no known vulnerabilities on 2026-11-17.

Coverage by area (newest first):
- `test_provider_status.py` — 11: Settings → Providers page + JSON twin,
  key values never leak into either surface, Brave reported as config-only,
  `enabled` follows the real factories (only the selected provider reads as
  enabled), cap state and last failure from persisted data
- `test_ai_generators.py` — 17: adapter output validation, key-in-header
  (D8), repair retry, citation integrity (P5), transient-failure retry vs
  immediate 400 failure, factory selection + template fallback
- `test_usage_caps.py` — 11: search/AI daily caps enforced (`0` blocks
  all), skipped search never yields `no_website_found`, unmetered sources
  uncapped, `ai_usage` written on success and failure
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

## Live smoke — Phase 2, keyed — 2026-11-10

Keys read from `.env` (booleans only — values never printed or logged).
Note: `python-dotenv` warns *"could not parse statement starting at line 29"*
— a comment lost its leading `#` (`Options: template | gemini | …`); harmless
(the line is skipped, nothing lost) but the `#` should be re-added.

- **Tavily (primary search) — PASS**: factory selected `TavilySearchProvider`;
  two research runs → **both `succeeded`**; `debian.org` → `reachable`
  (HTTP 200, real title, contact-path finding); identity findings carry
  **real Tavily source URLs**; `provider_usage tavily/search` = **one row,
  calls=2** — keyed same-day accumulation (H5). Quota spent: 2 of the 1,000
  free-tier credits (caps were not yet enforced at this point — enforced
  in the section below).
- **Serper adapter — PASS**: direct live call returned 3 real results.
- **Test-data artifact (not a defect)**: the prospect name "Debian Keyed
  **Smoke** Co" made the query match smoke alarms — the search engine
  answered the query it was given; keep test names free of the words being
  searched.
- **AI adapters — GAP, closed in the section below** (recorded here as the
  Phase-2 finding): with `AI_PROVIDER=gemini` and `GEMINI_API_KEY` set,
  `get_generator()` still returned `TemplateGenerator`
  (`app/ai/__init__.py` hardwired it) — the Phase-3 roadmap item *"gemini/
  groq/openrouter adapters with JSON validation, retry, usage logging"*
  and binding **D7** were unbuilt, `ai_usage` was never written, and all
  three AI keys were inert. **Fixed 2026-11-10** — see *AI adapters +
  enforced caps* below.

### Not verified in this pass (honest limits)

- Live external providers: **DDGS, Tavily, Serper and real website
  inspection verified live (2026-11-10)**; AI providers and OSM were not
  (adapters unbuilt / Overpass 406 at edge — both closed or recorded in
  the next sections).
- Backup **restore** and `alembic downgrade` (upgrade path tested).
- Non-loopback bind with token (startup refusal tested; live LAN exposure
  not exercised) — `Secure` cookie flag is off by design for loopback.
- Login rate limiting: none (documented in SECURITY §3).

## Live smoke — Phase 3: AI adapters + enforced caps — 2026-11-10

Keys from `.env` (values never printed); real Gemini calls, no money spent
beyond the free tier. Closes the Phase-2 AI gap and review finding **#15
(caps decorative)**.

**Adapters (D7)**

- `app/ai/providers.py`: `GeminiClient` (JSON `responseSchema`, key in the
  `x-goog-api-key` **header**, never the URL) and `OpenAICompatClient`
  (`/chat/completions` — Groq, OpenRouter, local Ollama).
  `AIGenerator` validates output with Pydantic, allows **one repair retry**
  on a validation failure, and rejects citations to finding ids that were
  never provided (P5) — an ungrounded draft can never be produced.
- `get_generator()` now honours `AI_PROVIDER`: `gemini`/`groq`/
  `openrouter`/`ollama` selected from config, **missing key or unknown name
  falls back to the template floor with a logged warning** (never silently
  labelled `ai`).
- **Live: `gemini-3.5-flash` returned a real draft** — `origin='ai'`,
  `grounding=[17,18]` (real finding ids), submit → approve both 303. The
  default `gemini-2.5-flash` was **retired for new keys** (404) — the
  default is now `gemini-3.5-flash`, documented as a known-rot risk;
  `AI_MODEL` overrides it.

**Caps (finding #15 — were decorative, now enforced)**

- `app/services/usage.py`: one place that reads `*_daily_cap` and writes
  `provider_usage` / `ai_usage`. Per provider per calendar day, summed
  across kinds; `used >= cap` blocks (so `0` blocks everything);
  unmetered sources (DDGS, OSM, BizData, site fetch) have no cap.
- Search: checked **before** the call → step reported
  `skipped (daily cap reached: tavily)`, counted in `rate_limited`, and
  (P6) a skipped search is never evidence for `no_website_found`.
- AI: `generate_draft` blocks at the cap with `429` (HTML page + API), and
  **the template floor is never capped** (it makes no external call, P11).
- `ai_usage` is now written for success *and* failure (repair retries and
  transport retries included) — the Usage page's AI table is no longer
  permanently empty.

**Live evidence (2026-11-10)**

| Check | Result |
|---|---|
| Gemini draft | ✅ `origin='ai'`, grounded in findings `[17, 18]`, submit → approve OK |
| AI usage accounting | ✅ `ai_usage gemini/gemini-3.5-flash calls=2 ok=1 failed=1 pt=1283 ct=941` (one call had failed live on a 503) |
| Transient 503 from Gemini | ✅ observed live → one bounded retry added (`TRANSIENT_STATUS` 429/500/502/503/504); non-transient (400) still fails immediately |
| Search cap hard stop | ✅ with `TAVILY_DAILY_CAP=2` and usage already at 2: research step reported `skipped (daily cap reached: tavily)`, `calls=2` unchanged, `rate_limited=1` |
| AI cap hard stop | ✅ with `AI_DAILY_CAP=1`: first draft 303, second → **429** "daily AI cap reached (1/1 calls today)", no extra call made |
| Settings → General | ✅ table shows cap / **used today** / configured with `2 / 2 — cap reached — calls blocked` and `AI (gemini) 2 / 5` |
| Settings → Usage | ✅ cap strip (Tavily `2 / 2 blocked`) + AI rows from the live `ai_usage` |
| Origin badge | ✅ message edit page shows `ai` badge + "AI-generated — verify every claim before sending (P5)" |

**Tests:** `tests/test_ai_generators.py` (17: parse, key-in-header, repair
retry, citation integrity, grounding-required, transient-retry, 400-not-
retried, shape failure, factory selection + template fallback) and
`tests/test_usage_caps.py` (11: cap blocks the call, `cap=0` blocks all,
under-cap accumulates, unmetered never blocked, skipped search ≠
`no_website_found`, AI cap blocks before any call, template never capped,
usage recorded on success and failure, `usage_summary`). The suite now has
an **autouse fixture that forces the template floor, clears every key and
disables keyless discovery** — a developer's real `.env` can never turn a
unit test into a live call (AGENTS §7).

### Not verified in this pass (honest limits)

- Groq / OpenRouter / Ollama adapters: **adapter-shape tested only**
  (MockTransport), not called live — one provider per smoke keeps spend
  bounded; each is the same `AIGenerator` over `OpenAICompatClient`.
- Gemini free-tier **rate limits** under sustained load (single calls only).
- `AI_MODEL` override against a second model id (the code path is tested;
  the value is config-driven).

## Known gaps (spec promises not yet built)

Two, both **explicitly deferred with a reason** (recorded here rather than
silently dropped; the matching spec text now says so):

1. **Providers page affordances** — the status page shipped 2026-11-11
   (read-only; it 404'd before, closing the Phase 2 exit criterion *"Settings
   → Providers status page"*). The UI spec's enable/disable **toggle** and
   **"Test" button** are deferred: the toggle persists through `save_settings`,
   which rewrites `.env` **and drops its comments** (owner decision needed),
   and the Test button makes a live paid provider call from the UI (needs a
   cap/consent decision).
2. **`GET /settings` index** — API_DESIGN §1 listed a settings index route;
   the nav has always linked straight to the sub-pages, so no index exists.
   Amended in API_DESIGN to say so.

Closed earlier and covered by tests: findings evidence CRUD, notes surface
(Flow 9), HTMX research-progress polling (`test_findings_crud.py`,
`test_notes.py`, `test_research_polling.py`), enforced daily caps
(`test_usage_caps.py`), AI adapters (`test_ai_generators.py`). The
`POST /prospects/{id}/findings` and `/research/status` rows in API_DESIGN
are live, not "not implemented".

## Phase 5 — Hardening & Release (complete)

- Security pass: all §9 abuse-case items tested and passing
- Failure-injection: provider 429/500/timeout → partially_completed, partial results preserved
- Performance: 5,000-prospect list <200ms, indexes verified via EXPLAIN QUERY PLAN
- Docs consistency: BizData added to SYSTEM_ARCHITECTURE diagram, DATABASE_DESIGN source_type enum, RESEARCH_FINDINGS table, COST_ANALYSIS table
- Tag `v0.1.0`: exists in git

## Visual redesign browser pass — 2026-11-11 (charcoal + signal orange, grouped sidebar)

- **Scope:** full UI re-skin (dark charcoal + orange accent) + top bar → grouped
  left sidebar. Spec amendments recorded in `UI_UX_SPECIFICATION.md`
  (opening, §1, §5, §10). No product/behavior changes; P1/P5/P6 verified in code.
- **Playwright browser pass** (18 screenshots, reviewed by the orchestrator):
  desktop 1440 (sidebar, dashboard pipeline/metrics, table + badge families,
  filter bar, form validation, focus ring = 2px `rgb(255,107,26)` outline + glow,
  toast, help panel, login + inline 401 error), icon rail at 1024, drawer at
  375 (closed/open — `aria-expanded`, focus → first link, backdrop,
  Escape → closed + focus restored, Tab wrap).
- **Contrast (independently scripted, WCAG formula):** all text pairs ≥4.5:1
  (worst: `--color-text-subtle` on raised = 4.97); accent-on-tint 5.47;
  control boundaries ≥3:1 after correcting `--color-border-strong` to `#6a7280`
  (handoff's `#3a3e46` claim was an arithmetic error: actual 1.56:1).
  Inputs/filter chips/toggle chips use the strong border (WCAG 1.4.11).
- **Reduced motion:** `--dur-*` compute to 0ms under
  `prefers-reduced-motion: reduce`; pulsing `running` dot static.
- **Deterministic:** `uv run pytest` 330 passed · `ruff check` / `ruff format
  --check` clean · `mypy app` clean · 61/61 CSS vars defined, 0 orphans ·
  0 old-palette refs, 0 `style=` attrs in templates, no `prefers-color-scheme`.
- **Owner decisions applied during review:** Docs nav link removed (`/api/docs`
  is an empty shell under the CSP — Swagger CDN blocked; revisit when a docs
  surface exists); per-row Merge button uses new `.btn.danger-outline` so solid
  red stays reserved for delete-grade actions.
- **Known:** one pre-existing flaky network test
  (`test_safe_fetcher_allows_public_url_validation`, DNS-timing; passes in
  isolation and on re-run). Synthetic verification prospects were created and
  removed (DB empty, `integrity_check ok`).
- **Pipeline-style dashboard refresh (2026-11-11):** the dashboard pipeline
  strip now renders as numbered flow cards (`01 QUALIFY → 02 OUTREACH →
  03 CONVERT`) with orange arrows and per-phase totals in the header —
  the moodboard's Nexus pattern, presentation-only (counts, links, stage
  order and P6 zero-state unchanged). Verified live at 1440 and 375 with
  3 synthetic prospects (1 transitioned to `researched` for mixed counts);
  screenshots reviewed, then rows deleted — DB back to `0 prospects`,
  `integrity_check ok`. Spec §2.1 updated in the same change.

## Pipeline Canvas V2 — automated contract pass — 2026-11-16

**Scope:** the canvas-only port specified in `docs/design/pipeline-canvas.md`
§V2 (owner-approved 2026-11-12). One screen — setup-card cluster, stage board,
outreach region — replaces the standalone pages; routes remain as deep links.
Test tracker: `docs/design/canvas-implementation-tasks.md`.

**Automated evidence** (`tests/test_canvas_contract.py`, 43 tests):

| Contract | Check | Status |
|---|---|---|
| Deep links (§V2.2) | Every retired setup route (`/`, `/status`, all `/settings/*`, `/merge/duplicates`) returns the canvas shell with the right card pre-opened (`data-panel-open`) | pass |
| HTMX branch | The same routes with `HX-Request: true` return the panel fragment only (no document/canvas chrome) | pass |
| Dual render | Plain GET and HX fragment render the same card heading/body (one partial source) | pass |
| Board fragment | `GET /prospects` + HX returns zones only; plain GET returns the full canvas | pass |
| Rail v2 | No settings/merge links in the rail; Canvas + Shortcuts/Sign out only | pass |
| Detail panels | `/prospects/new`, `/prospects/{id}`, `/prospects/{id}/edit`, `/prospects/{id}/research`, `/messages/{id}/edit` open the correct panel and render their content | pass |
| Saved views toolbar | Picker lists `/prospects/views/{id}/apply`; the filters panel renders the "Save this view" form when filters are active | pass |
| Filter chips + lens (B3) | Each active filter is a removable link that preserves the others; booleans stay truthy; stage lens renders a single matching zone and exposes **Show all zones**; nothing when unfiltered | pass — 5 tests |
| Filters panel | `/prospects/filters` opens the 360px panel, HX returns its body only, and the toolbar button carries the current query | pass — `test_filters_panel_and_stage_lens` |
| Outreach HX + panel posts (B4) | `/outreach` + HX returns the region fragment; panel forms `hx-post` to their own action (no 405 to the page URL); prospect form labels are explicitly associated; redirect `?flash=` confirmations render | pass — 3 tests |
| Performance | 5,000-prospect canvas render stays under the Phase 5 budget after removing the per-render fuzzy-duplicate scan | pass — `test_phase5_performance.py` |

**Responsive pass — 2026-11-16** (Playwright viewport emulation, local
screenshots in gitignored `.ui-qa/phase-b/`):

| Width | Evidence |
|---|---|
| 375 / 430 | Stacked flow; **119px toolbar**; setup cluster **2-column** (398px; board starts 657px); **68px cards with 12px gaps**, 12px setup gap; full card meta/status rows (mobile is never density-compact); no overflow/console errors |
| 768 | Stacked flow; **111px toolbar**; setup 348px (board at 547px); 12px card/setup gaps; no overflow |
| 1280 | One **49px** toolbar row (saved view hidden below 1280, available in Filters); board fit at 93% |
| 1440 | One **49px** toolbar row; board fit at **100%**; Home fits the whole world at 60% with no wire text; `?stage=` keeps all 10 lanes and dims the non-matching ones (no >125% auto-zoom) |

**Raw screenshots (gitignored):** `.ui-qa/phase-b/1440-filtered.png`,
`1440-overview.png`, `1440-filters-panel.png`, `1440-lens.png`,
`1280-filtered.png`, `768-filtered.png`, `430-collapsed.png`,
`375-collapsed.png`, `375-filters-panel.png`.

**Amendment 2026-11-16:** the stacked flow applies below **1024px** (was
768px) because the world cannot fit at the 0.5 zoom floor at 768. At phone
widths the setup cluster is a 2-column grid with **no wire notes**, saved
views live only in the Filters panel, cards keep full meta/status (never
density-compact), and the toolbar is 119px; first load fits the **board**
(Home fits the whole world). **Wire annotations and setup→pipeline connectors
are removed**; only orthogonal stage-flow connectors remain. Card/setup/
outreach gaps are 12/16/12px to prevent crowded or overlapping cards. The
stage lens keeps every lane visible and dims the non-matching ones (no auto-
zoom above 125%).

**Accessibility evidence — 2026-11-16** (Playwright + axe-core 4.10 + Lighthouse):

| Check | Result |
|---|---|
| axe-core WCAG 2.0/2.1 A+AA | **0 violations**, 24–25 passes at 1440 (canvas/chips), 375 (stacked), 375 + filters panel + Add-prospect panel, and `/outreach` + message panel |
| Lighthouse accessibility | **1.0** (desktop); best-practices 1.0; SEO 0.75 (only missing meta description) |
| Computed contrast | heading/muted 7.7:1 · subtle 6.03:1 · filter chip 5.47:1 · primary action 6.76:1 · zone title 7.7:1 (all ≥ AA) |
| Focus trap / Escape / backdrop | panel focus wraps; Escape and backdrop close and restore invoker focus |
| Dirty-form confirm | cancel keeps the panel + typed value; accept closes |
| Keyboard map | `?` help, `/` search, `j`/`k` highlight, `Enter` open card, `m` move menu, `g p` 86% / `g o` 100% / `g s` Status panel, `+`/`−`/`0`/`f`; plain wheel zooms at the pointer, Ctrl/Cmd+wheel pans (browser-verified) |
| Reduced motion | `prefers-reduced-motion: reduce` → 0.01ms transitions/animations |
| Prospect form labels | 10 fields associated with explicit `for`/`id`; `test_prospect_form_labels_are_associated` |

**Outreach interaction pass — 2026-11-16 (B4):** message card → panel →
pending approve moved the card to Approved and refreshed the region; the
approved panel offers only **Open in WhatsApp** (P1 — no auto-send); opened
message → **Confirm sent** recorded the confirmation and removed it from the
region. Fixed along the way: pan no longer captures setup/message link clicks;
panel forms `hx-post` to their own action (previously 405 to the page URL);
board/outreach regions refresh from HX fragments after panel posts.

**Regression note:** the first canvas pass called `find_fuzzy_duplicates()` on
every render as part of the setup-card meta line (~99 ms at 5,000 prospects,
of a 113 ms response). The Merge card now computes pairs when opened; the meta
line is a static advertisement. The Phase 5 budget is restored.

**Not yet verified (open):**
- Full manual re-walk of flows 0–11 after the canvas port (services are
  unchanged and all flow tests pass, but the UI paths moved).

## UX refinement pass — 2026-11-16

- **Canvas at 1280/1440/1920:** no horizontal overflow; board fills the
  viewport width at a readable 0.97–1.5×; selecting a card keeps it fully
  visible beside the 560px drawer; no text below 11px.
- **390/768:** stacked canvas, full card meta, no overflow; prospect/settings
  drawers are full-width (390) or 560px (768) with sticky headers.
- **Guards browser-verified:** reply hidden without a sent message and for
  terminal prospects; WhatsApp disabled without a phone; Generate draft
  disabled without a validated opportunity; View research disabled when no
  run; stage submit disabled until a transition is chosen.
- **Consistency:** Flow references removed; “candidates” → “prospects”;
  Merge node/drawer renamed Duplicates; links blue, filter/phase/region chips
  neutral, destructive actions outlined.

## Design-skill audit — 2026-11-16

**Impeccable detector** (1440/390; `/prospects`, `/prospects/1`, `/settings/profile`,
`/outreach`, `/settings/usage`): after fixes, no quality warnings. Remaining
`nested-cards` warnings are the board's intentional structure (stage lanes
containing prospect cards), not decorative card stacks.

**Taste Skill browser consistency** (1440/390): fixed the genuine findings —
clipped prospect meta, flat heading scale, off-screen keyboard focus, run-on
zone/message headings, missing description/share metadata, and sub-44px mobile
targets. Remaining FAILs are marketing-page rules that do not apply to a
full-bleed app canvas and are waived with reasons: container margins,
hero-screen fold, three intentional button variants, setup-card navigation
links, lane/card nesting, and "the section needs a visual" (the canvas is the
visual). Open WARNs: copy length, no marketing moment, logout form status line,
and label-centre/stranded-action on setup cards.

**Lighthouse after the pass:** accessibility 1.0 · best-practices 1.0 · SEO 1.0.

## Spacing / padding / alignment pass — 2026-11-16

- Adds the `--space-1…7` scale (4/8/12/16/24/32/48), 6/8/12 radii, and
  32/36px control heights; toolbar, rail, board nodes, cards, zoom controls
  and drawers use them.
- Prospect cards: 12px padding, 4px title→meta, 8px meta→footer, no fixed
  text heights; meta line-height 1.4 with descender padding; 24px pills with
  8px gaps and 4px wrap gap; 28px menus; wrapped pill rows align the
  today/menu group to the last row.
- Board: 24px between nodes, 48px lane rows, 12px label→row; fit-to-view
  includes Terminal + the full Messages lane with 48px padding; inventory
  gap is 48px; card focus is capped at 100–110%.
- Drawers: 16px outer padding, 16px card rhythm, 12px radius, 36px inputs,
  8px label/helper gaps, 16px field groups, 40px close column with 12px gap,
  scroll-only header shadow and dynamic scroll-padding; danger zone has a
  red inset card and aligned 36px controls.
- Browser-verified at 1280, 1440, 1920 and drawer widths 768/390.

## Card stage tag + header/copy cleanup — 2026-11-16

- Prospect cards: the stage tag moved from the footer pill row to the card's
  top-right corner as a compact outlined uppercase label in the stage hue
  (10px, 20px tall, 48% max width, ellipsis + title tooltip). The status dot
  moved into the footer badge row, vertically centered; compact density keeps
  a name-row dot copy so the hot signal survives when the footer is hidden.
  Wrapped pill rows still bottom-align the today/menu group. Card heights
  stay uniform (103px at 100%).
- Canvas toolbar: 52px row + hairline (rail head matched), 16px control gaps,
  title group left and saved-view/primary group right with a 24px gap before
  Add prospect. Saved view uses an uppercase micro-label; Filters count badge
  is 20px. Active filters get a full-width darker band with a top hairline,
  12px vertical padding, 13px/32px chips, and hairline-separated Clear all.
  Verified at 1470, 1280, 1024, 768 and 390; no horizontal overflow.
- Region labels are chip + count only ("PIPE · N on the board",
  "MSG · N in flight") because the toolbar h1 already names the surface.
- Copy audit removed self-referential panel subtitles (General, Status,
  Saved views), shortened Import/Usage/Profile/Filters intros, dropped the
  duplicate Retention section heading and threshold helper, removed the
  repeated Role column from the Discovery/Search provider tables, renamed
  the note field to "Note", the reply submit to "Save reply", the research
  jump link to "Go to research", the finding submit to "Save finding", and
  dropped the message-history "Status:" prefix (status stays in the sticky
  header). Retention's flagged-copy now says "longer than the current
  threshold" instead of the stale 12 months.
- Gate: 375 tests, ruff, format and mypy clean.

## Setup-panel layout + copy pass — 2026-11-16

- Providers: replaced the four stacked 6/7-column tables with one grouped row
  list (Discovery / Search / Website fetch / AI drafts). Each row is provider
  name + configured/enabled flags + one `used today / cap` ratio; last failure
  only renders when present. Keys/caps note and Advanced moved to a card
  footer that applies to every group. No horizontal scrolling at 560/390.
- Saved Views: compact create callout plus a row list (name + created date,
  filter chips, Load/delete). Removed the "Named views" heading and "Load a
  view…" helper; per-row Load is a secondary button.
- Profile: one fields card + one Discovery card; removed the duplicate
  "Finish your profile" banner and panel subtitle. The prerequisite is stated
  once beside the disabled Run discovery action. Discovery jobs render as
  compact run rows instead of a 5-column table.
- General: Follow-ups + Vocabulary merged into one Defaults card with Save/
  Cancel inside it; daily caps table drops the duplicated cap column and shows
  `used / cap` once; Brave reads "unmetered" instead of "key set".
- Retention: removed the duplicate panel subtitle and added a Sweep threshold
  heading; flagged table unchanged.
- Browser-verified at 1440 and 390; 375 tests, ruff, format and mypy clean.

## Message panel arrangement — 2026-11-16

- Header: `← Back to prospect` sits above the prospect name and its
  stage/message badges; the 40px close column stays clear. Back uses the
  existing `data-panel-back` behavior in panel mode.
- Draft editor: the character count moved from below the form to directly
  under the textarea, right-aligned.
- Message history: replaced the run-on sentence with label/value rows
  (Origin, Created, Approved, Opened, Send confirmed); the P5 AI warning is a
  separate note under the list.
- Canvas shell flash now renders strings only, so the full-page message deep
  link no longer prints the `OutreachMessage` ORM repr above the toolbar.
  Regression test added in `tests/test_canvas_contract.py`.
- Verified at 1440 and 390; 376 tests, ruff, format and mypy clean.

## Release-readiness pass — 2026-11-17

- Gate on the committed tree: **378 passed / 0 failed**; `ruff check`,
  `ruff format --check` and `mypy app` clean.
- Dependency audit: `uvx pip-audit` → **no known vulnerabilities** (2026-11-17).
- Fresh-install drill: empty SQLite file → `alembic upgrade head` → 20 tables
  at revision `3b7e5d9a1c42`; app boots against it.
- Access-gate smoke on the fresh DB with `APP_ACCESS_TOKEN` set:
  `/healthz` 200, `/login` 200, `/prospects` 302 to login, `/api/docs` 401.
- Backup drill: `python -m app.cli backup` writes a snapshot to `backups/`.
- Service packaging: Linux user units for app, worker and a daily backup timer
  added under `deploy/systemd/` with install/verify/remove instructions.
- Release process documented in `docs/RELEASE_CHECKLIST.md` (gate, migration
  and restore drills, doc consistency, tagging, rollback).
- **Fix: CSV row cap rejected instead of truncated.** `parse_csv` used to stop
  reading at `csv_max_rows`, so a 10,000-row file produced a preview whose
  "rows in file" number was wrong and whose overflow rows vanished without a
  word. It now raises a clear error (400 on both the import page and
  `/api/v1/imports/preview`), matching the panel hint and SECURITY §1/§9.
  Covered by `tests/test_csv_import.py::test_parse_csv_rejects_rows_over_the_cap`
  and `::test_row_cap_rejected_on_both_surfaces`.
- Docs reconciled: SECURITY §9 all eight abuse cases checked with their
  owning tests; §10 audit command corrected to `uvx pip-audit`; §4's nonexistent
  `DEBUG=true` guard removed; §3 now states plainly that the cookie `Secure`
  flag is hardcoded off and must be flipped for TLS/LAN deployments; test-count
  strings updated in `QA_CHECKLIST.md` and `design/pipeline-canvas.md`.

## Full browser QA pass — 2026-11-17 (findings + fixes)

Interactive pass over the running app (Chrome desktop + headless sweeps at
375/768/1024/1280/1320/1440), seeded synthetic DB: dashboard/status, filters
+ chips + saved views, add prospect, detail panel (research → findings →
reply → notes → suppression round trip), message draft → approve → open chain,
setup cards (Usage, Providers, Import, General, Retention, Saved views), board
refresh and error paths. **Six defects found and fixed:**

| # | Finding | Fix | Verified |
|---|---|---|---|
| 1 | First-load fit collapsed to the 0.5 floor (50–52% at 375–1440, `density-compact`) — a `8f97c7c` regression against §V2.1 "fits the board **width**, min 0.93, pan instead of shrinking" | `fitBoardReadable()` fits width only, floors at `MIN_READABLE = 0.93`, centres when it fits and flush-mounts at the 48px pad otherwise (`canvas.js`) | Headless: **93% + density-full at 375/768/1280/1320/1440**; pinned by `test_canvas_fit_keeps_the_readable_floor` |
| 2 | Any HTMX 4xx (e.g. "A reason is required to clear suppression", approval without acknowledgement) **wiped the panel** with a misleading "Couldn't load this panel. Check your connection" + Retry, destroying the form | `htmx:responseError` now leaves the panel alone for HTTP ≥400 — `beforeSwap` already toasted the server's real reason; only transport failures get the retry state | Live: empty-reason clear → toast shows the reason, panel keeps all 5 forms; `test_panel_http_errors_keep_the_form` |
| 3 | A redirect flash rendered **three times**: shell banner + two identical toasts (`message` and `?flash=` are the same string) | `partials/flashes.html` de-duplicates and skips the message the canvas banner already shows; the banner carries `role="status"` for the announcement | Live: notice count **1**, no toast container on canvas; `test_flash_query_renders_exactly_one_notice` |
| 4 | After a validation error every optional prospect field showed the literal text **"None"**; the error banner repeated the field name ("business name: business name is required") | Inputs render `… or ''` for `None`; the banner lists each validator message once (they already name their field) | `test_form_values_never_render_python_none`, `test_validation_error_banner_does_not_repeat_the_field`; live re-render clean |
| 5 | A required `Form(...)` missing from the POST returned a **raw JSON 422** and threw away the canvas (FastAPI rejects before the route's `field_errors` page) | `RequestValidationError` handler renders a readable 400 page (`templates/error.html`) for HTML posts; JSON/API keeps the 422 contract | Live: field removed → styled 400 page + "Back to the pipeline"; `test_missing_required_form_field_returns_readable_html` |
| 6 | Research panel only polled while `running`, so an open panel froze on **"queued / no findings"** after the worker finished | Polls while `queued` too, adds "waiting for the worker" copy, hides Re-run while queued | `test_queued_run_polls_for_the_worker` (page + fragment) |
| 7 | htmx settled the `style` attribute on board/outreach region swaps → **CSP violation after every `data-refresh-board` action** (blocked write; layout re-applied by `canvas.js`) | `htmx-config` meta settles `class/width/height` only — no `unsafe-hashes` added to the CSP | Live: reply → board refresh → **0 CSP violations**; CSP directive unchanged |

Also verified in this pass: filter apply/chip-remove/Clear all, saved-view
apply, add prospect (400 validation → create → `14 on the board` + detail
panel), reply recording (activity + stage unchanged from `converted`),
suppression set/clear round trip (guards remove Generate draft/WhatsApp),
message approve with ungrounded acknowledgement, Usage/Providers/Import/
General drawers, `.env` line-29 dotenv parse warning fixed, **Lighthouse
a11y 1.0 / best-practices 1.0 / SEO 1.0 with 0 failures** and **0 console
errors** on `/`, `/prospects`, `/outreach`, `/prospects/14/research`,
`/prospects/new`.

Gate: **385 tests**, `ruff check`, `ruff format --check`, `mypy app` clean.

### Known/observed, not defects

- Canvas zoom reached 200% once during the pass and was restored from the
  saved camera on later loads — 200% is the documented max (wheel/`+`), so this
  is a user-zoomed view being remembered; no code path was identified writing
  it, and it did not reproduce.
- "AI (template) → Configured: **no**" in General → Daily caps is literally
  correct (template needs no key) but reads oddly next to a working AI floor.
- Import shows the native file control ("Choose File / No file chosen") inside
  the drop zone — functional, styled only.
- "Run research" / "Archive prospect" post as plain forms (full navigation)
  while every other panel action is HTMX; each lands on a sensible page, so it
  is an inconsistency rather than a bug.
- Long stage names truncate on the card tag by design (48% max + title
  tooltip), e.g. "OPPORTUNITY IDE…".


## Borderless + label + motion pass — 2026-11-17 (owner directives)

Seven element comments plus three system directives were taken as one pass:

| Directive | What shipped |
|---|---|
| "arrange element properly" — Danger zone | Blocks share one head layout: `h4` + status badge + reason inline (the `Status:` line is gone); fields and actions align the same way in all three blocks |
| "cleanup" — Activity | `payload\|tojson` replaced by `describe_payload()`: `reason: asked to be removed`, `run id: 5 · status: succeeded · steps: search ok, inspect failed` — never a JSON blob, wrapped on its own line |
| "cleanup labels … all pages" (decision: **drop them**) | Self-evident card headings removed from detail, message and research panels (Key facts, Research, Opportunities, Outreach, Notes, Activity, Record reply, Follow-ups, Draft editor, Grounding, Send chain, Message history, Research status, Findings). Headings kept only where they group controls (Defaults, Daily caps, Backup, Discovery, Danger zone, Add finding, flow steps) |
| "arrange element properly" — Outreach | State row (status + origin + grounding) → message preview (left-rule block) → actions; no more badges inside an `action-group` and no `Message` definition row |
| "fix the ui issue" — suppression banner | Moved **below** the sticky header in detail and message panels, so the header (and close button) own the panel's top row |
| "arrange element properly" — detail head | One wrapping row: name + badges inline, `Edit details` opposite and centred; head padding tightened. Titles are now `<h2>` (axe `heading-order` back to 1.0) |
| "too many bright colors in all cards" (decision: **neutral tags, colour = signal**) | Every prospect-card chip neutral (`raised` fill + muted text); red only for suppression/failure, amber for `check failed`; tints brightened so borderless chips still read as blocks |
| "another design entirely for the tags that bends with the card" (decision: **edge-fused tab**) | Stage pill → sunken corner tab: negative margins pull it onto the card edge, radius `0 8px 0 6px` matches the card, no outline; `do not contact` keeps the danger tint |
| "remove the border outline from all elements, use shade contrast" | Full borderless pass: cards, zones, panels, drawers, badges, chips, buttons, inputs, banners, tooltips, menus, tables are fill-only. Ladder `#090a0c → #171a20 → #1f232a → #292d35` + `--color-field #0e1014`; `--color-border` demoted to dividers (no `border: 1px solid var(--color-border)` box survives in either stylesheet — pinned by a test); focus rings and error borders untouched (≥3:1) |
| "some of the canvas is too fast, make it smooth" | `--dur-base` 150→200ms, new `--dur-slow` 320ms + `--ease-out` decelerating curve; drawer slide and sidenav use slow, card snap-back uses base, toast slide eases; `prefers-reduced-motion` zeroes the new token too. Pan/zoom stays untransitioned (direct manipulation, §2.1) |

Also fixed while verifying: research findings tables squeezed their statement
column into a ribbon — statement cells now hold a 240px measure and the table
scrolls (`.panel-body` min-width 680px).

**Verification:** headless captures at 1440 (board, suppressed detail 3000px
tall, settings, research) and 390; Lighthouse **a11y/best-practices/SEO 1.0
with 0 failures on both `/prospects` and `/prospects/11`**; 0 console errors
on `/`, `/prospects`, `/prospects/11`, `/prospects/14/research`,
`/settings/general`, `/outreach`, `/prospects?category=hotel`; gate **392
tests**, ruff/format/mypy clean. Contract tests added: dropped labels, `<h2>`
titles, human payloads, neutral-vs-signal chips, fused stage tab, no box
outlines, smooth motion.


## Canvas usability pass (owner directives) — 2026-11-17 (part 2)

| Directive | Shipped |
|---|---|
| "remove all em dashes between text" | Every rendered route checked: `/prospects`, `/prospects/11`, `/settings/general`, `/settings/providers`, `/outreach`, `/settings/import`, `/settings/usage`, `/prospects/14/research` return **0 em dashes**. Templates, JS strings, service/route messages and the QA DB's provider statements converted (`·` for separators, `.`/`;` for clauses). Python/JS/template *comments* keep them (never rendered); pinned by `test_rendered_pages_carry_no_em_dashes` |
| "btn that properly arranges the elements when they are scattered" | New **Arrange** control (`data-canvas="tidy"`) beside zoom/fit: clears saved zone positions → default layout → fit the world. `PlCanvas.tidy` exported; pinned by `test_arrange_control_and_layout_reset` |
| "resize to mobile and back → connecting lines scattered, no reset" | `canvas.js` now listens to `resize`: wires always redraw (rAF), and crossing the 1024px stacked/wide breakpoint drops the saved coordinates (they only fit the layout they were dragged in), re-runs `applyDefaultLayout()` and fits. No viewport-resize tool exists in this browser harness, so the fix is pinned at source level (same test) and should get one manual desktop check |
| "make the cards on canvas bigger with more spacing in and out" | `--card-h` 72→**84px**, `--card-gap` 12→**16px**, `--zone-pad` 8→**12px**, `--zone-w` 264→**288px** (JS `ZONE_W` mirrored), card inset token `--card-pad` (16px, 12px compact — the fused stage tab follows it), message cards 16px padding, setup grid 248px columns with 24px gaps. `test_cards_have_more_room` |
| "remove unnecessary texts e.g. No notes yet." | Dropped: `No notes yet.`, `No activity yet.` (card renders only with activity), `No findings yet. Run research…`, `Generate draft is disabled…` (the disabled button's title/aria says it), `Not researched yet. Use Run research above…`, the whole send-chain paragraph (the confirm dialog carries the warning), the `—` placeholder cell in Import |
| "clean up the Danger zone text" | `Reason (required)`; stage label removed (placeholder `Move to…`), button `Move`; `Archive` (was "Archive prospect"); `Block outreach` (was a second "Do not contact"); status stays inline in the block head. `test_danger_zone_copy_is_not_repetitive` |
| "remove unnecessary text / labels / elements project-wide" | Sweep: providers notes → `·`, usage `· blocked`, filters/import/general copy rewritten, spec jargon `(P4)`/`(P5)` dropped from user-facing sentences, activity payloads already human, message panel hint placed **outside** the button row, message panel now disables Open-in-WhatsApp without a phone (it was the only surface letting the click fail server-side) |
| "sidebar: proper grouping and separation" | Drawer gained a **Session** group label with one hairline above it (`.rail-foot` border always on; removed the double rule inside the Sign-out form). `test_nav_marks_the_current_screen` |
| "active state is not working, only hover styling" | Server marks `Home` active on `/` + `/prospects` and `Messages` on `/outreach` (Status already); canvas view clicks sync `.active` across the rail/drawer |
| "setup card: no underline on hover, add hover states" | `.canvas-screen a:hover { text-decoration: none }` + setup card = fill step + 1px lift + `--shadow-1`; message cards gained the same fill hover |
| "filter bar overlapping the top bar" | The active-filter band is now a **sibling row below** the toolbar (was a flex child with `order:10`), verified at 390/768/900/1280/1440. `test_active_filter_band_sits_outside_the_toolbar` |
| "message head: arrange + tag design (and everywhere else)" | Both heads: name left, **tags riding the right edge**, actions beside them (`.badge-row { margin-left: auto }`), back link on its own line |

Verification: headless captures at 1440 (board, filtered band, 2400px detail,
message) and 390; gate **398 passed** (64 in the canvas contract suite),
ruff/format/mypy clean. Manual checks still open: Arrange click
and the mobile→desktop resize need a real viewport (no resize tool in this
harness).


## Critique fixes (impeccable pass) — 2026-11-17

Source: `.impeccable/critique/2026-11-17T11-58-49Z__127-0-0-1-prospects.md`
(dual-agent, 32/40; detector: 4 warnings → 2 unique, both adjudicated false
positives). Owner answered: fix all five priority issues in order, stage tab
only where it carries information, sync URL + aria-current.

| Priority issue | Fix | Verified |
|---|---|---|
| P1 · research table scrolls Verification/Actions out of reach | Findings restacked into rows: statement on its own line, then kind/verification/confidence/origin + source + Verify/Edit/Delete (`.finding-list`) | Screenshot: Verify visible without horizontal scroll; contract tests green |
| P1 · approval queue clipped from the default fit | Toolbar carries `Pending review · N` with a warn dot; clicking it pans to the messages region (`data-canvas-view="outreach"`) | `test_toolbar_surfaces_the_approval_queue`; chip renders `· 1` live on every canvas page |
| P1 · "you are here" disagreed with itself | SSR marks exactly one view (`/`→Home, `/prospects*`→Board, `/outreach`→Messages) with `aria-current`; view clicks now move `.active` **and** `aria-current`, update the `h1`, and `history.replaceState` the path (query preserved) | Live: `/`→Home, `/prospects`→Board, `/outreach`→Messages with h1 "Message pipeline", one `aria-current` per page, 0 console errors on 6 routes; `test_nav_marks_the_current_screen` |
| P2 · approval copy contradicted P1 and differed per panel | Both panels confirm with *"Approve this draft? Opening WhatsApp is a separate step; you press Send."*; the ungrounded acknowledgement renders **only** when the draft has no grounding | `test_approval_gate_copy_and_ack_follow_grounding` (both surfaces, both states) |
| P2 · suppressed prospect said "you can't" three times | Banner keeps the reason + gains **`Undo: Clear suppression`** beside it; the two muted restatements are gone; the Opportunities and Outreach cards (whose only content was the restatement) are skipped while suppressed, and the Outreach tab is hidden so no anchor dangles | Live screenshot + `test_danger_zone_copy_is_not_repetitive` |
| Owner decision · stage tab | Renders only in the Terminal frame | `test_stage_tab_only_where_stages_share_a_frame` (terminal has it, lane card doesn't) |

Bonus fix found while testing: the detail panel never received
`grounded_findings` (only the message routes computed it), so "Grounded in"
was dead there and the acknowledgement condition could not work — the detail
route now queries it from `active_message.grounding`.

Gate: **400 tests**, ruff/format/mypy clean; 0 console errors on 6 routes.


### Owner follow-up directives — 2026-11-17 (post-critique panel polish)

| Directive | Change | Verified |
|---|---|---|
| "remove Do not contact" | The `h4` sub-heading is gone; the block reads by its status badge + action (Danger zone frame carries the section) | Live panel + `test_danger_zone_copy_is_not_repetitive` |
| explanation "should be above the btn" | "Blocks drafts, approvals and WhatsApp actions. Clearing requires a reason." moved inside the form, directly above `Block outreach` | Live panel + contract assert (paragraph index < button index) |
| "Archive the btn should be in front not under" | Archive/Restore form now rides the `.danger-block-head` row beside the heading (flex row; head h4 margin zeroed so the pair centers) | Regex assert: button inside the head div; live panel |
| ""Not suppressed" in front not above Reason (optional)" | Badge moved onto the label row: `Reason (optional) [Not suppressed]` (head exists only while suppressed) | Exact-string assert; live panel |
| "rmove this Overview Research Outreach Activity" | `.detail-tabs` nav removed from the template and its CSS rules deleted; sections reached by scrolling | `assert "detail-tabs" not in html`; live panel |
| Gate after pass | 401 tests, ruff/format/mypy clean, assets `?v=61` | full suite |


## Critique run 2 fixes (impeccable pass) — 2026-11-18

Source: `.impeccable/critique/2026-11-18T05-13-18Z__127-0-0-1-prospects.md`
(dual-agent, 30/40; trend 32 → 30; detector 4 warnings, all adjudicated false
positives/defensible). Owner pre-authorized: "proceed wit th fix" — all five
priority issues in order, no question round.

| Priority issue | Fix | Verified |
|---|---|---|
| P1 · `.pending-chip` rendered as a raw UA button (run-1 regression: markup without CSS) | Token styles in canvas.css (field fill, radius-md, control-h-md, mono label, 7px warn dot, accent focus-visible) | CDP computed style: mono 11px, field bg, radius 8, dot 7×7 warn; contract test |
| P1 · one zoom-out click stripped every safety badge (0.93/1.25 = 0.744 < floor) | `density-compact` hides only `.pc-meta`; the status foot (badges + due text) stays; compensating name-row dot rule removed; spec §2.5 amended | CDP at 74%: foot display=flex, "check failed" badge visible, meta none; `test_compact_density_never_hides_status_foot` |
| P1 · default framing sliced the pending card at the edge / cut regions with no affordance | `.world-edges` overlay: 64px gradient to bg on each overflowing edge, classes from the content union (world box is 0×0 — absolute children) | CDP: `has-edge-t/r/b` set, `l` correctly off; pixel-probed right edge 31,35,42 → 9,10,12 over exactly 64px; `test_world_edge_fades_mark_cuts` |
| P2 · documented `/` shortcut dead on the canvas | Falls through to clicking `.filter-open`, then focuses `#panel-root input[name=q]` when the HTMX body lands (poll ≤2s); help row now says "opens Filters" | CDP: keydown `/` → panel open, activeElement=`fp-q`, Escape closes; `test_slash_shortcut_opens_filters_panel` |
| P2 · chip count stale after approval (outside both swap targets) | Chip always rendered (`hidden` at 0) + `syncPendingChip()` on every outreach swap recounts zone cards, updates text/aria/hidden | CDP real flow: approve pending message → PlBoard.refresh → chip hidden, count 0, zone 0; `test_pending_chip_is_token_styled_and_live` |
| Heuristic-3 · Arrange discarded manual layouts with no confirm | `data-confirm` on the tidy button + capture-phase click handler for non-form controls (forms keep submit confirm; in-form controls skipped — no double prompts) | Contract test; existing confirms audited (all in-form, unaffected) |
| Heuristic-10 · gestures absent from help | Help table gains Wheel (zoom / Ctrl+wheel pans) and Drag background rows | `test_help_table_documents_gestures_and_search` |
| Minors | `.canvas-screen` gets `role="region" aria-label="Pipeline canvas"` (spec §6.3); dead `<g data-system-wires>` + 8 `data-wire-target` attrs + "wired to the region it feeds" comments removed; h1 CSS comment rewritten to match the sans rule; zone-title spec drift recorded in §3.2 | Contract tests |

Corrected during adjudication: A's "`Generate draft` has no stated reason"
— verified live, `title="Validate at least one opportunity first"` is
present (tooltip, hover-only). Not a defect.

Gate: full suite + ruff/format/mypy; detector re-run over changed templates.


## Responsive layout system (adapt pass) — 2026-11-18

Owner brief: "no UI consistencies, not proper spacing, hierarchy is poor,
everything competing for attention… make it tab-based on the views that have
no canvas as default view." Built in three phases, all shipped together.

| Phase | Change | Verified |
|---|---|---|
| 1 · Retired views → tab pages | `/settings/*` + `/merge/duplicates` = one settings tab page (8 tabs, deep link = active); `/prospects/{id}` = detail tab page (page head owns name/badges/Edit, `#detail-*` anchor tabs, Outreach tab hides when suppressed); `/status`, edit, research, message = generic standalone pages. Home stays canvas + Status card; Filters + Add prospect stay canvas action panels. Card click / `g s` / rail Status navigate; view buttons navigate when no canvas; HX fragments kept for parity | Contract tests (`test_retired_routes_render_standalone_pages`, settings/detail tab tests, nav-source pins); live: card click → `/prospects/12` with tabs, rail Status → `/status`, Board button → canvas, Filters panel opens, 0 console errors |
| 2 · Phone triage mode | Work-first order (`applyStackOrder`, DOM follows visual, wide restores); Add prospect → 44px header icon, toolbar two rows, header title deduped; touch floor 44px (`pointer:coarse` or <768) incl. panel list rows + nav-close/panel-close; 16px gutters, zone gaps 12px, sticky zone heads; every panel table carries `data-label` (the <640 transform existed but no table had labels) | CDP 390: first zone y **813 → 281** (work at 33% of viewport), setup last, toolbar 2 rows, header-add 44×44, **0 under-44 targets on /prospects**, 1 remaining = inline prose link (WCAG exception), no h-scroll |
| 3 · Tablet grid | 768–1023: rail + board as 2-column zone grid, bands span both columns, grid gap owns spacing | CDP 768: zone tops pair (2 cols), rail visible, toolbar keeps Add, no h-scroll |

Geometry/ASCII verified at 390 / 768 / 1440 (wide order restored: setup →
board → messages). Detector: same 4 adjudicated warnings, none new.
Gate: 413 tests, ruff/format/mypy/node --check clean, assets `?v=64`.
Docs: pipeline-canvas (§V2.2 contract, §4 panels, §5 mapping, §6.1, §1.3),
API_DESIGN (two route families), UI spec (deep links, tree, §2.3, §7),
USER_FLOWS wording, canvas-implementation-tasks amendment.

- **Scroll fix (2026-11-18):** stacked views (<1024) no longer bind the wide
  wheel/pan/zoom-key gestures — wheel scrolls the page (measured: wheel no
  longer canceled at 760/390, zoom/density stay 93%/full, cards stop
  expand/collapsing); wide ≥1024 keeps wheel-zoom (owner spec, measured
  93%→58% density flip); stacked card drag still moves stages (new → replied
  at 390, seed restored); mode-crossing fit runs wide-only so stacked entry
  can't overwrite the stored wide view. Test: test_stacked_view_scrolls_instead_of_zooming.

- **Element-comment batch (2026-11-18):** Stage override heading moved into
  the control row (heading + Move to… + Move on one line) · Filters panel URL
  explainer removed · board PIPE region label (chip + hint) removed and
  applyStackOrder re-anchored to #board-root (work-first verified: setup last
  at 390, canonical order at 1440) · detail outreach groups get 16px vertical
  gaps (measured 38/35px between group starts) · reply helper copy sits after
  its label (LABEL>P>TEXTAREA) · danger heading rides the block head in both
  states (in front of the status/label text, #danger-zone id kept) · Block
  outreach right-aligned (btnRight == formRight) · terminal sub-stage badges
  became a "Filter stage" dropdown linking the three stage lenses · message
  approve checkbox + button on one line (scoped `.action-inline`; blanket
  `.action-group form` rule collided with the existing `display: inline` —
  repaired the corrupted `.empty .action-group` selector in the same pass) ·
  Sign out pinned to the nav bottom (foot bottom + 16px padding == nav bottom
  at 1440/760/390) · phase tabs: 4 tabs, 1 visible group at 390, all 4 at 1440.
  Test: test_panel_directive_batch_2026_11_18.

- **Phase-tabs round 2 (2026-11-18):** Messages + Setup joined the strip as
  tabs M/S (wrappers outside #board-root; media-scoped hiding keeps the wide
  canvas identical — setup/msg rects match the pre-wrapper baseline at 1440),
  tab gap 4→12px, and the stacked compact card became content-driven
  (`height: auto` + `min-height`): the fixed 84px height pushed the
  `check failed` badge 9px out of the card at the owner's stored 50% zoom —
  verified gone in their own tab (compactOverflow []), each of the6 tabs shows
  exactly its own panel at 390/760, strip hidden + all6 groups visible at
  1440, 0 console errors. Test: test_phase_tabs_group_the_stacked_board.

- **Card click → side panel on desktop (2026-11-18):** owner "when i click on
  card on canvas it opening the panel in full screen for desktop instead of
  the side panel". board.js now opens the detail as the 560px slide-over when
  `innerWidth >= 768` (with the card panned into view via focusCard) and only
  navigates to the full-screen detail page on the phone. Verified by CDP:
  1431px → panel w560 sideSheet (canvas intact, URL unchanged), 768px → same,
  390px → PlPanel.open NOT called (navigate branch). Deep links still land on
  the detail tab page at any width.
