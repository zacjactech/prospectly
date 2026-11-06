# Implementation Roadmap

Phased plan with **testable exit criteria** per milestone. Each phase ends with: green test suite, updated docs, working demo of its flows. No phase starts before the previous phase's criteria pass.

**Effort scale:** S ≤ half day · M 1–2 days · L 3–5 days (solo developer, incl. tests).

---

## Phase 0 — Environment & project skeleton (S–M)

*Note: runtime is Python 3.15.2 via the Python Install Manager; `winget` is broken and the `python` alias hangs on this machine (see `TECHNOLOGY_DECISIONS.md` D14).*

- [x] Install Python 3.12+ (Python Install Manager — `winget` broken, D14) and `uv`; verify versions.
- [x] `git init`; `.gitignore` (data/, logs/, backups/, uploads/, .env, __pycache__, .venv); initial commit → private repo `github.com/zacjactech/prospectly`.
- [x] Project scaffold: `app/` package layout per `SYSTEM_ARCHITECTURE.md` §2; `pyproject.toml`; pinned deps; `ruff` + `mypy` config; `pytest` harness.
- [x] Config module (pydantic-settings) + `.env.example`; FastAPI app boots at `127.0.0.1:8000` with `/healthz`.
- [x] Alembic initialized; baseline migration creates empty schema; `app.cli backup` command stub.
- [x] CI-less quality gate documented in README (`ruff check`, `ruff format --check`, `mypy`, `pytest` — run via `py -m uv run ...`).

**Exit criteria:** `pytest` passes (smoke: app boots, healthz 200); `ruff`/`mypy` clean; fresh clone → README steps → running app in <10 minutes.

## Phase 1 — Prospect management (M)

- Models: `sources`, `prospects`, `tags`, `notes`, `activities` (+ migrations).
- Services: create/edit/archive, dedupe keys (exact + fuzzy flag), stage transitions with validation, activity logging.
- Pages: prospect list (filters/search/URL state), manual add/edit, detail shell with tabs, empty states; JSON API `/api/v1/prospects*`.
- CSV import: upload → preview → commit with per-row outcomes; backup reminder.

**Exit criteria:** tests for dedupe (exact blocked / fuzzy flagged), stage machine, import parsing (valid/dup/error rows); golden test: add → edit → archive → history intact; XSS payload in name renders escaped.

## Phase 2 — Research pipeline (L)

- `SafeFetcher` (all SSRF controls) + `SiteInspector` (outcome classification incl. `no_website_found` vs `check_failed` per P6).
- Job queue (`jobs` table + worker loop + retries/backoff) and `research_runs` with step reporting.
- `SearchProvider` adapters: Tavily (primary, free tier), Serper (optional key), ddgs fallback (best-effort, never required).
- `findings` CRUD with evidence fields, verification/confidence/origin invariants; website-check history UI.
- Provider usage accounting (`provider_usage`) + Settings → Providers status page.

**Exit criteria:** unit tests for SafeFetcher (SSRF checklist in `SECURITY_AND_PRIVACY.md` §9), outcome classification matrix, job retry/reclaim; integration: fake providers → run completes → findings with sources; real-provider smoke test optional/manual (documented).

## Phase 3 — Opportunities + outreach + WhatsApp (L)

- Rule engine producing evidence-linked `opportunities` (+ validation guard).
- AI abstraction (`app/ai/`): interfaces, `template` adapter (no-AI floor), `gemini`/`groq`/`openrouter` adapters with JSON validation, retry, usage logging.
- Draft generation grounded in findings; draft editor; full approval state machine with guards (suppression, grounding ack).
- `app/whatsapp.py` (normalize + `wa.me` build) + Open-in-WhatsApp flow + **explicit confirm-sent** step + default follow-up suggestion.
- Outreach queue page; prospect Outreach tab; activity types for the whole chain.

**Exit criteria:** state-machine tests (every illegal transition → 409); suppression blocks all outbound paths; golden path test end-to-end with fake AI: add → research(fakes) → opportunity validate → generate → edit → approve → open (URL asserted, status `opened_in_whatsapp`) → confirm → `sent_confirmed` + activity + `last_contacted_at`; phone normalization table-driven tests (NG formats).

## Phase 4 — Follow-ups, replies, dashboard, polish (M)

- Follow-ups CRUD + due/overdue views + sweep job; reply recording; not-interested/do-not-contact flows end-to-end (Flow 6–8).
- Dashboard metrics (exact definitions from `PRODUCT_BRIEF.md` §8) + "needs attention" queue.
- Usage page; general settings (vocabulary, defaults); toasts/flash across pages; keyboard shortcuts; responsive pass (§7 of UI spec).

**Exit criteria:** metrics tested against seeded fixtures (counts match definitions); overdue logic timezone-tested (UTC storage, local display); a full manual walkthrough of all 10 user flows recorded in `docs/QA_CHECKLIST.md` with no blockers.

## Phase 5 — Hardening & release (M)

- Security pass: execute every item in `SECURITY_AND_PRIVACY.md` §9 as automated tests where feasible; CSP/CSRF/session verified; log redaction test.
- Failure-injection pass: provider 429/500/timeout → partial results preserved, UI explains.
- Performance sanity: 5,000-prospect list query <200ms locally (indexes verified via `EXPLAIN QUERY PLAN`).
- Docs final review: contradictions sweep across `docs/` (§9 of the discovery process); README setup re-run from scratch on a clean profile.
- Tag `v0.1.0`.

**Exit criteria:** all §9 checklist items green; fresh-machine setup verified; docs consistency review signed off.

## Phase 6+ — Deferred (design now, build later; not MVP)

1. Email channel behind same approval gate (`Channel` boundary).
2. Google Places discovery provider (billing-gated; storage-rule compliance coded then).
3. Browser-assisted research (Playwright adapter for JS-heavy sites).
4. Fuzzy merge UI, saved views, retention sweep (auto-flag inactive >N months).
5. **Hosted multi-user:** PostgreSQL migration (checklist: freeze writes → alembic to PG → ORM-level dump/restore → `DATABASE_URL` swap → smoke suite → enable auth + `owner_id` scoping), Docker packaging, optional WhatsApp Business Platform adapter behind the same approval gate.

---

## Milestone → dependency map

```
P0 ─▶ P1 ─▶ P2 ─▶ P3 ─▶ P4 ─▶ P5
              └─ AI adapters (template floor) usable from P3 on
P6 items are individually releasable after P5
```

## Risk register

| Risk | Mitigation |
|---|---|
| Free-tier changes (search/AI) mid-build | provider abstraction + template adapter floor; usage page shows breakage early |
| OSM coverage weak in Ikorodu | manual/CSV remains primary discovery; Places provider ready when billing accepted |
| Windows environment quirks (paths, ACLs, no Python yet) | Phase 0 handles install; path handling via `pathlib` everywhere; CI-free quality gate runnable in one command |
| Scope creep toward "full CRM" | `PRODUCT_BRIEF.md` §6 exclusions are binding; new features require a doc amendment |
| WhatsApp policy shifts | click-to-chat is official functionality; no unofficial tech to break; channel abstraction limits blast radius |
