# Technology Decisions

Each decision lists options considered, trade-offs, evidence reference, the decision, and consequences. This document is the **binding stack authority** — implementation must not deviate without recording an amendment here.

---

## D1 — Backend framework

**Decision: FastAPI** (Python 3.12+)

| Option | Why not / why yes |
|---|---|
| FastAPI ✅ | Typed request/response models (Pydantic) double as AI structured-output validation; async fits concurrent provider calls; auto OpenAPI for the JSON API; active in 2026 as the default for new API projects ([comparison evidence](RESEARCH_FINDINGS.md#1-python-backend-frameworks)) |
| Flask | Least machinery, but validation, schemas, and docs are all DIY — more glue code long-term for the same result |
| Django | Excellent batteries (auth, admin, ORM) — all three largely unused in a single-user local MVP; Celery-oriented job story invites unnecessary infra |

**Consequences:** we assemble SQLAlchemy + Alembic + Jinja2 + session auth explicitly (all small, standard choices). One `uvicorn` process runs the app.

## D2 — Frontend approach

**Decision: Server-rendered Jinja2 templates + HTMX + vanilla JS (Alpine.js allowed for local state). No Node build step in MVP.**

| Option | Why not / why yes |
|---|---|
| HTMX + Jinja2 ✅ | Product is tables, forms, filters, inline edits — hypermedia's exact profile; zero build pipeline; templates live next to Python ([evidence](RESEARCH_FINDINGS.md#2-frontend-options)); solo-maintainable |
| React + Vite | Justified only by rich client-side state; would add a second language/toolchain, dev-server choreography, and CORS/session complexity for no MVP feature |
| Next.js | SSR/hosting conventions aimed at deployed web products; wrong shape for a localhost tool |

**Escape hatch (documented, not built):** a single page can be migrated to React later without touching the API — the JSON API (D3) exists partly for this.

## D3 — API surface

**Decision: Dual surface over one service layer.**
- `/pages/*` → server-rendered HTML (with HTMX partial responses for in-page updates).
- `/api/v1/*` → JSON REST for prospects, research, outreach, jobs, metrics — used by tests, automation, and any future UI.

Business logic lives in `app/services/*`; neither surface may contain domain rules. This satisfies `API_DESIGN.md` without duplicating logic.

## D4 — Database

**Decision: SQLite (WAL mode) via SQLAlchemy 2.0 + Alembic; PostgreSQL as the documented deferred target (roadmap Phase 6+, "hosted shape").**

- Evidence: [research §3](RESEARCH_FINDINGS.md#3-database).
- Rules that keep the migration cheap: ORM-only queries (no raw dialect SQL in services), portable column types (no SQLite-only extensions), JSON stored via SQLAlchemy `JSON` type, ISO-8601 UTC timestamps as TEXT/DateTime consistently.
- Consequences: single-writer assumption (fine for one user); backup = file copy via a `backup` command; no connection pool tuning needed.

## D5 — Background processing

**Decision: Database-backed job queue with an in-process asyncio worker. No Celery, no Redis, no broker.**

- Jobs table (`jobs`) with `status`, `run_after`, `attempts`, `locked_at`; a worker loop inside the FastAPI lifespan claims due jobs with a transaction (`UPDATE ... WHERE status='queued'` + row lock semantics via `BEGIN IMMEDIATE` or optimistic claim).
- Retry: exponential backoff with jitter, max 3 attempts, then `failed` with last error surfaced in UI.
- Why: research runs take seconds-to-minutes and must survive restarts (durable in SQLite); a broker would violate P12.
- Limitation acknowledged: multiple app processes would break job claiming — single process is a documented constraint until Postgres (where `FOR UPDATE SKIP LOCKED` is available).

## D6 — Research provider abstraction

**Decision: `app/providers/` package with protocols + adapter classes.**

```
providers/
  discovery/   DiscoveryProvider  → osm_overpass, google_places, manual
  search/      SearchProvider     → tavily, serper, brave, ddgs_fallback
  inspect/     SiteInspector      → httpx_inspector (only impl in MVP)
```

- Each adapter: explicit `timeout`, `max_retries`, per-provider rate limiter (token bucket persisted in `provider_usage`), and normalized result objects. Providers are enabled only when their env var key exists (feature-flag by configuration, D8).
- Failures return typed `ProviderError` values — services decide fallback (e.g., search fails → skip AI grounding rather than crash).

## D7 — AI provider abstraction

**Decision: `app/ai/` with `Summarizer`, `OpportunityAnalyzer`, `MessageGenerator` interfaces; adapters `gemini`, `groq`, `openrouter`, `ollama`, `template`.**

- Config picks provider+model per task (`AI_PROVIDER=gemini`, …). `template` adapter = placeholder-based drafts with **zero external calls**, guaranteeing the product works with no AI key (cost principle P11).
- All AI calls: JSON-schema-validated output (Pydantic), one repair-retry on validation failure, usage tokens logged to `ai_usage`, and every produced finding/draft carries `origin='ai'`, `confidence='low'` until a human verifies it (P5).
- Prompt inputs are **only** stored findings/fetches (grounding), never ungrounded web content, and each generated claim must cite finding IDs or be marked hypothesis.

**Implementation status (2026-11-10):** `MessageGenerator` has concrete adapters —
`template` (floor), `gemini` (`x-goog-api-key` header, native JSON
`responseSchema`), and `groq`/`openrouter`/`ollama` via the OpenAI-compatible
`/chat/completions` shape. Every call validates output with Pydantic, allows
**one** repair retry, rejects citations that were not provided (P5), returns
usage in `GeneratedDraft.meta`, and the service writes `ai_usage` for
successful *and* failed calls. `Summarizer`/`OpportunityAnalyzer` remain
interfaces only (opportunities come from the rule engine, D6); AI
summarization is not part of the research pipeline yet.

## D8 — Configuration & secrets

**Decision: `pydantic-settings` reading environment + `.env` file (gitignored); `.env.example` committed with placeholders.**

- API keys never appear in code, DB, logs, or URLs.
- `config.py` exposes typed settings grouped by section (app, db, providers, ai, whatsapp, limits).
- Paid providers off by default; enabling requires key + explicit `*_ENABLED=true`.
- **User profile** lives in `settings` (or the dedicated `profiles` table per `DATABASE_DESIGN.md` §2.15): offer, target industries/categories, locations, ideal-client signals. Drives automated discovery jobs and personalizes outreach drafts.
- **CSV import** is demoted to a secondary Settings utility (upload → preview → commit); it is not part of the default discovery flow.

## D9 — WhatsApp integration boundary

**Decision: `app/whatsapp.py` — a pure module: `normalize_phone()`, `build_wa_me_url()`. It returns strings; the route opens the OS browser. No network calls, no WhatsApp SDK.**

- Guarantees P3/P4 by construction: nothing in this module can send anything.
- Phone normalization: strip formatting → require an international format (country code present; an optional configured default country supplies it for bare national numbers) → validate with `phonenumbers` library → store E.164.

## D10 — Logging & observability

**Decision: stdlib `logging` with structured (JSON-ish) line format to `logs/app.log` + console; per-request ID; per-provider call log (provider, duration, status, tokens) written as `provider_usage` rows; a Settings → Usage page renders it.**

- No telemetry leaves the machine. Log rotation by size (stdlib `RotatingFileHandler`).

## D11 — Error handling

**Decision: layered.**
1. Provider adapters catch network/HTTP errors → typed errors with retryability flag.
2. Services map errors to domain outcomes (`research_run.status='failed'`, `last_error`).
3. FastAPI exception handlers: 4xx → page-level inline error; 5xx → friendly error page with request ID; never leak stack traces or keys to HTML.
4. User-triggered actions validate preconditions (suppression, approval state) and return actionable messages ("This prospect is marked do-not-contact").

## D12 — Testing strategy

**Decision: pytest as the only test framework.**

| Layer | Approach |
|---|---|
| Unit | Pure functions (phone normalization, dedupe keys, URL building, rule-based opportunity heuristics, status transitions) — no I/O |
| Service | In-memory/tmp-file SQLite, fake provider adapters (injected), assert state machines & guards (approval gate, suppression blocks) |
| API | `httpx.ASGITransport` against the app; JSON API contract tests |
| Integration | One "golden path" test: add prospect → fake research → approve → wa.me URL built → confirm sent → activity logged |
| External | Never hit real providers in tests; adapters have `Fake*` implementations |

Coverage target: meaningful branches of `services/` and `whatsapp.py` at ≥80%; overall percentage is not a goal in itself. `ruff` for lint/format; `mypy` (strict on `app/` core modules).

## D13 — Explicit non-decisions (kept open on purpose)

| Item | Status |
|---|---|
| Hosted deployment / Postgres / multi-user | Phase 6+ (deferred) — design only, no code commitment |
| Email channel | Deferred; `Channel` boundary kept in outreach design |
| WhatsApp Business Platform | Deferred to scalable tier; cost documented |
| Browser-assisted research (Playwright) | Deferred; inspector interface allows a second impl later |
| Lighthouse scoring | Deferred; not needed for MVP opportunity rules |

## D14 — Development environment (observed 2026-11-06, Phase 0)

**Interpreter & tooling (as installed):**

- **Python 3.15.2** (owner decision: "work with the 15.2") at `C:\Users\User\AppData\Local\Programs\Python\Python315\python.exe`, installed via the **Python Install Manager 26.4** (MSIX from python.org). `pyproject.toml` keeps `requires-python = ">=3.12"`; the runtime floor is unchanged.
- **`winget` is unusable on this machine** — v1.2.10691 crashes with `0xC0000005` on every install. Use python.org / direct downloads instead; do not script against winget here.
- **`uv` 0.13.7**, installed with `py -m pip install uv` (→ `...\Python315\Scripts\uv.exe`). `uv` is **not on PATH**; invoke it as `py -m uv` or by full path. `uv.lock` pins the whole dependency tree.
- Legacy `C:\Windows\py.exe` launcher still present (pymanager warns to remove it to enable its own `py`); `py -3.15` works, so `py` is the reliable entry point.

**Known hazard — hanging `python` alias:**

- The `python.exe` / `python3.exe` app-execution aliases shipped by the `PythonSoftwareFoundation.PythonManager` package **hang indefinitely** (even `python --version < NUL` never returns). They are the PATH-visible `python`, so *bare* `python` is unusable.
- Because uv's interpreter discovery walks `PATH`, any uv command that must *search* for an interpreter (fresh directory without `.venv`) hangs too.
- **Workaround (verified):** pass the interpreter explicitly once — `uv sync --python "$env:LOCALAPPDATA\Programs\Python\Python315\python.exe"` — after which `.venv` short-circuits discovery and every `uv run` / `uv sync` works normally. Never rely on bare `python`; use `py`, the full interpreter path, or the project `.venv`.

**FastAPI ≥ 0.162 route-annotation rule (verified on 0.162.0 + pydantic 2.14.3):**

- FastAPI infers a route's `response_model` from its **return annotation** unless that annotation subclasses `starlette.Response`. Annotating a handler with the template helper value (`templates.TemplateResponse` — a *bound method*, not a class) makes pydantic build an arguments schema for it and the app crashes at import with `NameError: name 'Request' is not defined`.
- **Rule:** page handlers annotate `-> HTMLResponse` and pass `response_class=HTMLResponse`; API handlers annotate the actual Pydantic response model (or a `Response` subclass). See the bullet in `AGENTS.md` §5.

**Other observations:**

- Node v26 exists but is intentionally unused (D2); PowerShell execution policy blocks `npm.ps1` — reinforces the no-JS-toolchain decision.
- `git init` done; repo-local identity = GitHub noreply (`185103884+zacjactech@users.noreply.github.com`); initial commit pushed to the private repo `github.com/zacjactech/prospectly` (Phase 0 close).
