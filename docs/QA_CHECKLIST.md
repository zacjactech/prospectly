# QA Checklist — Phase 4

Full manual walkthrough of the user flows (USER_FLOWS.md). Each flow is
verified against the running app at `http://127.0.0.1:8000/` and backed by
regression tests in `tests/`. "Blocked" means a defect with no workaround.

Run the app: `py -m uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`

---

## Flow 0 — Configure your profile (one-time setup)

| Step | Check | Status |
|------|-------|--------|
| Open Settings → Profile | page loads, form present | pass |
| Save offer / industries / locations / signals | values persist, flash shown | pass |
| Daily caps + providers shown read-only | values match config | pass |

## Flow 1 — Discover prospects automatically (default path)

| Step | Check | Status |
|------|-------|--------|
| Profile configured with signals | drives discovery queries | pass* |
| "Run discovery" from profile | discovery job queued | pass* |
| Candidates appear in Prospects list | stage = new | pass* |

\* Requires a configured search provider key; verified via unit tests with fakes.

## Flow 2 — Research a prospect (core evidence loop)

| Step | Check | Status |
|------|-------|--------|
| Click "Run research" on a prospect | run created, status running | pass |
| Findings stored with source URL + origin | evidence retained (P7) | pass |
| Website status classified | no_website_found vs check_failed distinct (P6) | pass |
| Stage advances new → researched | state machine enforced | pass |

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

## Flow 5 — Draft → approve → WhatsApp → confirm sent (the heart)

| Step | Check | Status |
|------|-------|--------|
| Generate draft | template floor, origin = template | pass |
| Edit text | demotes to draft (§3.1) | pass |
| Submit for review | draft → pending_review | pass |
| Approve blocked without grounding / ack | 409 | pass |
| Approve with acknowledgement | draft → approved | pass |
| Open in WhatsApp | wa.me URL built, never sent (P4) | pass |
| Confirm sent | sent_confirmed + activity + last_contacted_at + stage contacted | pass |
| Not sent | opened_in_whatsapp → approved | pass |

## Flow 6 — Reply handling

| Step | Check | Status |
|------|-------|--------|
| Record reply | activity reply_received, stage → replied | pass |
| Interested / Not interested / Do not contact | stage transitions enforced | pass |

## Flow 7 — Follow-up

| Step | Check | Status |
|------|-------|--------|
| Add follow-up with due date | row created, status open | pass |
| Due today counts as "due" | dashboard metric | pass |
| Overdue (due_date < today) | dashboard metric, timezone-tested (UTC) | pass |
| Complete / cancel | status updated, activity logged | pass |

## Flow 8 — Opt-out / do not contact

| Step | Check | Status |
|------|-------|--------|
| Set do_not_contact | suppression banner shown | pass |
| Draft / approve / open blocked while suppressed | 403 on all outbound paths | pass |
| Clear requires explicit action + reason | audited | pass |

## Flow 9 — Conversion

| Step | Check | Status |
|------|-------|--------|
| Set stage converted | prospect leaves active counts | pass |
| Handoff note recorded | note attached | pass |

## Flow 10 — CSV import (secondary Settings utility)

| Step | Check | Status |
|------|-------|--------|
| Upload CSV | preview: valid / duplicate / error rows | pass |
| Commit | valid rows inserted, dupes skipped | pass |

\* Verified via Phase 1 import tests; UI reachable under Settings.

## Flow 11 — Review & correction day (maintenance loop)

| Step | Check | Status |
|------|-------|--------|
| Dashboard "Needs attention" queue | overdue → pending review → failed research → unverified findings | pass |
| Fix unverified findings | Verify action available | pass |
| Approve pending drafts | Outreach queue | pass |
| Clear overdue follow-ups | Follow-ups page | pass |
| Settings → Usage | provider errors visible | pass |
| Backup | snapshot written to backups/ | pass |

---

## Dashboard metrics (PRODUCT_BRIEF §8)

| Metric | Definition | Status |
|--------|------------|--------|
| Total prospects | non-archived count | pass |
| Researched | ≥1 succeeded research run | pass |
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
| No auto-send / bulk WhatsApp | pass (wa.me URL only, human presses Send) |
| Human approval before outbound | pass (state machine, confirm-sent sole writer) |
| Suppression blocks all outbound | pass (draft / approve / open blocked, 403) |
| No invented facts | pass (AI origin = unverified, low confidence) |
| No duplicates | pass (partial unique indexes) |
| Illegal stage / message transitions | 409 |

---

## Test suite

`py -m uv run pytest` — **122 tests pass** (ruff, ruff format, mypy all clean).

Regression coverage added this phase:
- `tests/test_dashboard_metrics.py` — 11 metric-definition tests incl. timezone-tested overdue
- `tests/test_phase3_routes.py` — outreach golden path, suppression 403, illegal-transition 409
- `tests/test_whatsapp.py` — 38 phone normalization + wa.me URL cases
- `tests/test_opportunity.py` — 9 rule engine + validation tests
- `tests/test_outreach.py` — 9 state machine tests
- `tests/test_phase5_security.py` — 12 security abuse-case tests (SSRF, XSS, log redaction, non-loopback bind, CSRF, CSV guards, do-not-contact suppression)
- `tests/test_phase5_failure_injection.py` — 6 failure-injection tests (search 500/429/timeout, discovery failure, all-providers-fail)
- `tests/test_phase5_performance.py` — 3 performance tests (5,000-prospect list <200ms, index usage)
- `tests/test_csv_import.py` — 6 CSV import tests (parsing, dedupe, commit)
- `tests/test_bizdata_provider.py` — 6 BizData provider tests (mock transport, dedupe, error handling)

## Phase 6+ Free Features

| Feature | Description | Status |
|---|---|---|
| Saved Views | Save/reload filtered prospect list views | ✅ Implemented |
| Fuzzy Merge UI | Review and merge duplicate prospects | ✅ Implemented |
| Retention Sweep | Auto-flag inactive prospects (>12 months) | ✅ Implemented |

## Manual QA Walkthrough

All 11 user flows (USER_FLOWS.md) were manually walked through against the running app at `http://127.0.0.1:8001/` with no blockers. Flows marked `pass*` require a configured search provider key for automated discovery; discovery job queuing and candidate review are verified via unit tests with fakes.

## Phase 5 — Hardening & Release (complete)

- Security pass: all §9 abuse-case items tested and passing
- Failure-injection: provider 429/500/timeout → partially_completed, partial results preserved
- Performance: 5,000-prospect list <200ms, indexes verified via EXPLAIN QUERY PLAN
- Docs consistency: BizData added to SYSTEM_ARCHITECTURE diagram, DATABASE_DESIGN source_type enum, RESEARCH_FINDINGS table, COST_ANALYSIS table
- Tag `v0.1.0`: exists in git


