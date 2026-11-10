# System Architecture

## 1. Overview

Single-process local web application:

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (localhost)                                            │
│  Jinja2 pages + HTMX partials + vanilla JS                      │
└───────────────┬─────────────────────────────────────────────────┘
                │ HTTP (loopback only)
┌───────────────▼─────────────────────────────────────────────────┐
│  FastAPI process (uvicorn)                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ routes/     │  │ services/    │  │ jobs worker (asyncio)  │  │
│  │  pages      │→ │  domain      │← │ claims jobs from DB    │  │
│  │  api (JSON) │  │  logic ONLY  │  └───────────┬────────────┘  │
│  └─────────────┘  └──────┬───────┘              │               │
│                          │                      │               │
│  ┌───────────────────────▼──────────────────────▼────────────┐  │
│  │ providers/                 app/ai/                        │  │
│  │  discovery (bizdata, osm)   summarizer / analyzer /        │  │
│  │  search (tavily, serper,   generator adapters            │  │
│  │          brave, ddgs)      (gemini/groq/openrouter/      │  │
│  │  inspect (httpx)           ollama/template)              │  │
│  └───────────────────────────────────────────────────────────┘  │
│  app/whatsapp.py  (pure: phone → wa.me URL; no sending)         │
└───────────────┬─────────────────────────────────────────────────┘
                │ SQLAlchemy ORM (transactions)
┌───────────────▼─────────────────────────────────────────────────┐
│  SQLite file (WAL)  data/prospectly.db   + Alembic migrations   │
└─────────────────────────────────────────────────────────────────┘
```

No other runtime dependencies: no Redis, no Celery, no message broker, no microservices (justified: single user, single machine, jobs are durable in the DB already — D5).

## 2. Module boundaries

| Module | Responsibility | May depend on | Must NOT contain |
|---|---|---|---|
| `app/routes/pages` | HTML rendering, form parsing, flash messages | services | domain rules, SQL |
| `app/routes/api` | JSON contract, status codes, validation | services | domain rules, SQL |
| `app/services` | Domain logic + state machines + transactions | models, providers (interfaces), ai (interfaces) | HTTP concerns, template rendering |
| `app/models` | SQLAlchemy ORM entities | — | business logic |
| `app/schemas` | Pydantic DTOs (API + AI structured outputs) | — | ORM models (explicit mapping) |
| `app/providers/*` | External I/O adapters with limits/retries | httpx, config | domain decisions (they return data/errors) |
| `app/ai/*` | AI interfaces + adapters + prompts | schemas, config | direct DB access |
| `app/jobs` | Queue claim/heartbeat/retry loop (discovery + research jobs) | services | business rules of its own |
| `app/whatsapp` | Phone normalization, `wa.me` URL building | phonenumbers | any network I/O |
| `app/templates` + `static` | Presentation | context from routes | business logic in Jinja (no complex expressions) |

**Dependency rule:** arrows point inward — routes → services → (providers/ai/models). Enforced by review + a lint-level import-linter contract (optional Phase 1 tooling).

## 3. Core state machines

### 3.1 Outreach message status (per message)

```
draft ──submit──▶ pending_review ──approve──▶ approved ──open──▶ opened_in_whatsapp
  ▲                    │                        │                     │
  └───edit─────────────┘ (returns to draft)     │                     │
                                                │            user confirms send
                                                ▼                     ▼
                                          (approved stays)     sent_confirmed ──▶ terminal
```

- Only `approve` (explicit button, requires non-empty final text) moves to `approved`.
- `open` builds the `wa.me` URL and sets `opened_in_whatsapp` — it does **not** imply sending (P4).
- `sent_confirmed` requires a second explicit button ("I have sent this message") and writes an activity entry with timestamp (P2).
- **Not-sent path:** from `opened_in_whatsapp`, "Not sent" returns the message to `approved` (ready to re-open); any text edit demotes to `draft` (approval is invalidated by edits) and requires re-submission/re-approval. There is no path to `sent_confirmed` other than explicit confirmation.
- Suppression (`do_not_contact`) blocks `approve` and `open` for the prospect.
- A prospect may have several message versions (one active at a time); creating a new draft while `sent_confirmed` exists requires an explicit "start new outreach" action that is guarded by stage rules.

### 3.2 Prospect stage (per prospect)

```
new → researched → opportunity_identified → outreach_ready → contacted →
replied → interested → follow_up_due ⇄ replied → converted
                 ↘ not_interested        ↘ do_not_contact (terminal)
```

- Stage changes are written to `activities` (append-only history).
- `do_not_contact` is terminal and blocks all outbound actions.
- Archiving (`archived_at`) hides from lists but preserves history.
- **Amendment (2026-11-10, owner-approved with the H2/H3 fix):**
  - *Event edges* — confirming a send (→ `contacted`, Flow 5.8) and
    recording a reply (→ `replied`, Flow 6.2) may land from any active
    stage: these are operator-recorded facts and the pipeline position may
    lag because stages are otherwise advanced manually.
  - *Dispositions at any point* — `not_interested` and `do_not_contact`
    are reachable from every active stage (Flow 6.3 / Flow 8 "Any time").
    `converted → do_not_contact` is allowed (opt-outs can arrive later);
    `converted → not_interested` is not.
  - *Flag sync* — moving the stage to `do_not_contact` sets the
    `do_not_contact` suppression flag (and clearing restores the
    pre-suppression stage from the activity log), so the stage view,
    badges, dashboard counts and outbound guards can never disagree (P10).

### 3.3 Research run status

`queued → running → succeeded | partially_completed | failed`

- `partially_completed` = some providers failed but findings exist (evidence preserved either way).

## 4. Data flow examples

### 4.1 Profile-driven automated discovery (default path)

1. Operator configures their profile in Settings (offer, target industries/categories, locations, ideal-client signals).
2. "Run discovery" → service creates a `discovery_job` row keyed to the profile.
3. Worker executes discovery steps: search queries (enabled SearchProvider) → optional Overpass/Places enrichment → candidate businesses.
4. Each candidate → `prospect` row with `stage=new`, `source=<provider>`, activity `discovered`; duplicates flagged via dedupe keys; prospects awaiting review appear in the Prospects list.
5. Operator reviews candidates (keep/edit/archive) → then runs research per prospect (Flow 3).

### 4.2 Research a prospect

1. User clicks "Run research" → service creates `research_run` + `jobs` row (type `research`).
2. Worker claims job (transaction), sets run `running`.
3. Worker executes pipeline steps: website inspection (httpx) → search queries (enabled SearchProvider) → optional discovery enrichment.
4. Each result → `findings` rows with source metadata (P7); site check → `website_checks` row (status classification per P6).
5. Optional AI summarization (if configured) → additional findings with `origin='ai'`, `verification_status='unverified'`.
6. Run marked `succeeded`/`partially_completed`; prospect stage → `researched`; UI polls HTMX partial for progress.

### 4.3 Draft → approved → WhatsApp → confirmed

1. "Generate draft" → service gathers approved/reliable findings → `MessageGenerator` (AI or template) → `outreach_messages` row (`draft`, `origin` recorded).
2. User edits text freely (edits logged) → clicks **Submit for review** → `pending_review`.
3. User clicks **Approve** → `approved` (guard: suppression check, non-empty text, ≥1 grounding finding or explicit "ungrounded" acknowledgement checkbox).
4. User clicks **Open in WhatsApp** → server returns/redirects `wa.me` URL → browser opens WhatsApp with prefilled text → status `opened_in_whatsapp`.
5. User clicks **I sent this message** → `sent_confirmed` + activity + `last_contacted_at` + follow-up default date suggested.

### 4.3 Follow-up

- Follow-ups are rows with `due_date`; dashboard "due today/overdue" query.
- Completing a follow-up → activity entry; prospect can be moved to `not_interested` / `do_not_contact` at any point (terminal guards apply).

## 5. Concurrency & transactions

- All multi-row writes (stage change + activity + counters) happen inside one SQLAlchemy session transaction.
- Job claiming uses a short transaction (`BEGIN IMMEDIATE` on SQLite) so two workers can't take the same job; single-process worker makes this belt-and-braces.
- Provider calls happen **outside** DB transactions (no network inside a transaction); results are persisted after, with idempotency keys (finding dedupe by `(prospect_id, source_url, finding_key)`).

## 6. Background processing strategy

| Concern | Approach |
|---|---|
| Trigger | Explicit user action or scheduled follow-up sweep (60s interval loop) |
| Durability | Jobs in SQLite; restart resumes `queued`/`running` (stale `running` >10 min reclaimed) |
| Retries | Exponential backoff + jitter, max 3, then `failed` with visible error |
| Rate limits | Per-provider token bucket (persisted counters) + `asyncio.sleep` pacing; global daily caps configurable |
| Timeouts | Per-step timeout (default 60s); job-level deadline 15 min |
| Observability | `provider_usage` rows (provider, calls, duration, status, tokens) + job attempts history |

## 7. Provider & AI abstraction boundaries

```python
# conceptual interfaces (implementation defines exact signatures)
class DiscoveryProvider(Protocol):
    async def discover(self, query: DiscoveryQuery) -> list[DiscoveredBusiness]: ...


class SearchProvider(Protocol):
    async def search(self, query: str, *, max_results: int) -> list[SearchResult]: ...


class SiteInspector(Protocol):
    async def inspect(self, url: str) -> SiteInspection: ...


class MessageGenerator(Protocol):
    async def generate(self, ctx: OutreachContext) -> GeneratedDraft: ...
```

Rules:
- Adapters are the *only* modules importing provider SDKs/HTTP shapes.
- Config selects implementations; tests inject fakes.
- New provider = new adapter file + config entry; zero service changes.

## 8. WhatsApp integration boundary

`app/whatsapp.py` exposes exactly two pure functions (D9):

```python
def normalize_phone(raw: str, default_country: str | None = None) -> str | None: ...  # → E.164 digits; bare national numbers rejected unless a default country is configured
def build_wa_me_url(e164: str, text: str) -> str: ...  # → https://wa.me/...?text=...
```

The route layer opens the URL via `webbrowser.open()` (or returns it for the user's click). The module imports no HTTP client; nothing in the codebase posts to WhatsApp.

## 9. Security-relevant architecture (summary — details in `SECURITY_AND_PRIVACY.md`)

- Bind to `127.0.0.1` by default; opt-in LAN bind requires an access token setting.
- All outbound fetches go through one `SafeFetcher` (scheme allowlist `http/https`, DNS resolution checked against private/loopback ranges, redirect cap, size cap, timeout) to prevent SSRF.
- CSRF token on every state-changing form; Jinja2 autoescaping on; CSP header set.
- CSV import (secondary Settings utility): size cap, encoding sniff, cell sanitization, no formula execution (export-side escaping too).
- Secrets only via env; redaction filter on log formatter for key-like values.

## 10. Configuration management

| Source | Precedence | Contents |
|---|---|---|
| Defaults in `config.py` | lowest | ports, timeouts, limits |
| `.env` (gitignored) | middle | keys, flags, paths |
| Environment variables | highest | CI/test overrides |

Settings are read once at startup (with explicit `reload` for dev). `.env.example` documents every key with safe placeholders.

## 11. Future hosted shape (design only, Phase 6+)

- Swap `DATABASE_URL` → PostgreSQL; worker claim → `FOR UPDATE SKIP LOCKED`.
- Add auth (session + password) and per-user scoping (`owner_id` column already reserved in schema design).
- Static assets served by any reverse proxy; app remains one service — still no microservices without clear justification.
- WhatsApp Business Platform would arrive as a `Channel` adapter behind the *same* approval gate — approval semantics never change.
