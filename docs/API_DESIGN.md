# API Design

Two surfaces over one service layer (D3):

1. **Pages** (`/`) — server-rendered Jinja2 + HTMX partials for the human UI.
2. **JSON API** (`/api/v1`) — programmatic contract for tests, automation, and any future UI.

Conventions (both surfaces): JSON API uses standard HTTP status codes; timestamps ISO-8601 UTC; errors as `{ "error": { "code", "message", "details?" } }` (FastAPI `HTTPException` paths — including the access gate's `401 {"detail": …}` — return bare `{"detail": …}`); list endpoints support `?page=&page_size=&sort=&filter...`; **all state-changing JSON endpoints require header `X-CSRF-Token`** (from cookie) — session auth is cookie-based: when `APP_ACCESS_TOKEN` is set, a login gate at `GET/POST /login` issues the session (see `SECURITY_AND_PRIVACY.md` §3).

---

## 1. Pages (HTML)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/prospects` | List (filters: stage, tag, category, location, search, include-archived) |
| GET/POST | `/prospects/new` | Manual add form |
| GET/POST | `/settings/import` · `/settings/import/commit` | CSV upload + preview + confirm **(secondary Settings utility; not part of discovery flow)** |
| GET | `/prospects/{id}` | Detail (tabs: Overview · Research · Opportunities · Outreach · Activity) |
| GET/POST | `/prospects/{id}/edit` | Edit |
| POST | `/prospects/{id}/archive` · `/unarchive` | Soft archive |
| POST | `/prospects/{id}/research/start` | Queue research run |
| GET | `/prospects/{id}/research/status` | HTMX partial: run progress + findings (polled every 2s while the run is `running`; terminal renders drop the `hx-*` attrs and polling stops) |
| POST | `/prospects/{id}/findings` · `/findings/{fid}/edit` · `/verify` · `/delete` | Findings evidence CRUD — create forces `origin=human`/`unverified`/`low`/`source_type=user` (P5); edit keeps `origin`, audits `edited_by` + first-edit `original_statement` for AI rows; delete 409 while linked to an opportunity (P7) |
| POST | `/prospects/{id}/notes` · `/notes/{nid}/edit` | Notes add/edit (Flow 9 handoff note; HTML surface only) |
| POST | `/prospects/{id}/opportunities` · `/{oid}/validate` · `/{oid}/dismiss` | Opportunity CRUD |
| POST | `/prospects/{id}/draft/generate` | Create draft (AI or template) |
| POST | `/messages/{id}/edit` · `/submit` · `/approve` · `/open` · `/confirm-sent` | Approval gate chain |
| POST | `/prospects/{id}/stage` | Manual stage change (validated) |
| POST | `/prospects/{id}/reply` | Record inbound reply (sets stage `replied`) |
| POST | `/prospects/{id}/follow-ups` · `/follow-ups/{fid}/complete` | Follow-ups |
| POST | `/prospects/{id}/do-not-contact` · `/prospects/{id}/do-not-contact/clear` | Suppression (flag + stage; clear requires `reason`) |
| GET | `/follow-ups` | Due/overdue list |
| GET | `/settings/providers` (HTML) · `/api/v1/settings/providers` (JSON) | Provider status: configured/enabled/usage/cap/last failure — **never the key values** (§2.8) |
| GET | `/settings/usage`, `/settings/general`, `/settings/import`, `/settings/retention`, `/settings/profile` (POST `/settings/profile`) | Usage, general settings, CSV wizard, retention, user profile (there is no `/settings` index — the nav links to each sub-page) |
| POST | `/settings/profile/discovery/run` | Queue profile-driven discovery (Flow 1: `discovery_job` + `jobs` row; processed by the worker) |
| GET/POST | `/settings/saved-views` · POST `/{id}/delete` | Saved views CRUD — filters whitelisted to the list query keys (unknown keys → 400) |
| GET | `/prospects/views/{id}/apply` | Redirect to `/prospects?` with the view's filters |
| GET | `/healthz` | Liveness (no auth, no data) |

HTMX partials return HTML fragments (`HX-Request` header honored); full page loads return complete templates. Flash/toast messages via `HX-Trigger` headers.

---

## 2. JSON API `/api/v1`

### 2.1 Prospects

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/prospects` | `stage`, `tag`, `category`, `location`, `q`, `archived`, `page`, `page_size` | `ProspectList` |
| POST | `/prospects` | `ProspectCreate` | `201 Prospect` · `409 duplicate` (with `existing_id`) · `422 validation` |
| GET | `/prospects/{id}` | — | `Prospect` (with counts) |
| PATCH | `/prospects/{id}` | `ProspectPatch` | `200 Prospect` |
| DELETE | `/prospects/{id}` | `?confirm=erase` | `204` (explicit erase path, cascades) |
| POST | `/prospects/{id}/archive` · `/unarchive` | — | `200 Prospect` |
| POST | `/prospects/{id}/merge` | `{target_prospect_id}` | `200 Prospect` (re-links children, logs) |

**Duplicate response example**

```json
{ "error": { "code": "duplicate_prospect",
             "message": "A prospect with the same phone already exists",
             "details": { "existing_id": 42, "match_type": "phone" } } }
```

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/settings/profile` | — | `UserProfile` (offer, industries, locations, signals) |
| PATCH | `/settings/profile` | `UserProfilePatch` | `200 UserProfile` |

### 2.2 Import

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/imports/preview` | multipart CSV (≤2 MB) | `ImportPreview {rows, valid[], duplicates[], errors[]}` |
| POST | `/imports/commit` | `{source_id?, rows[]}` | `ImportResult {created, skipped_duplicates, flagged_for_review}` |

### 2.3 Research

| Method | Path | Returns |
|---|---|---|
| POST | `/prospects/{id}/research/runs` | `202 {run_id, job_id}` |
| GET | `/research/runs/{run_id}` | `ResearchRun {status, steps, error, timings}` |
| GET | `/prospects/{id}/findings` | `Finding[]` (query filters `kind`, `verification_status`, `origin`) |
| POST | `/prospects/{id}/findings` | `201 Finding` (`source_type='user'` path; `origin`/`verification_status`/`confidence` are forced server-side) |
| POST | `/findings/{fid}/edit` | `Finding` — `statement`/`detail` only; `origin` preserved, `edited_by` + first-edit `original_statement` audit |
| POST | `/findings/{fid}/verify` | `Finding` — `verification_status → verified` (human action; idempotent) |
| POST | `/findings/{fid}/delete` | `204` — `409` while linked to an opportunity (evidence must stand behind it) |
| GET | `/prospects/{id}/website-checks` | `WebsiteCheck[]` history |

### 2.4 Opportunities

| Method | Path | Returns |
|---|---|---|
| GET/POST | `/prospects/{id}/opportunities` | list / create (`origin='human'` path) |
| POST | `/opportunities/{oid}/validate` · `/dismiss` | updated row (guards: needs evidence links) |

### 2.5 Outreach (approval state machine)

| Method | Path | Preconditions | Returns |
|---|---|---|---|
| POST | `/prospects/{id}/drafts` | not suppressed (`403`); stage allows outreach (`409`); AI cap not reached (`429`); provider call succeeds (`502` on adapter failure) | `201 OutreachMessage` (`origin` = `ai` or `template`); AI spend is written to `ai_usage` even when generation fails |
| PATCH | `/messages/{id}` | status ∈ {draft, pending_review, approved, opened_in_whatsapp}; `sent_confirmed` is terminal and rejects edits (`409`) | any change to `current_text` demotes status → `draft` (edits invalidate approval) |
| POST | `/messages/{id}/submit` | non-empty text | `pending_review` |
| POST | `/messages/{id}/approve` | `pending_review`; not suppressed; grounding ok or ack set | `approved` + `approved_at` |
| POST | `/messages/{id}/open` | `approved` | `opened_in_whatsapp` + `{wa_me_url}` (**never marks sent**) |
| POST | `/messages/{id}/confirm-sent` | `opened_in_whatsapp` (or explicit `force` + reason) | `sent_confirmed`; side effects: activity, `last_contacted_at`, stage `contacted`, default follow-up suggestion |

**Error codes:** `409 invalid_transition`, `403 suppressed` (`do_not_contact`), `422 missing_grounding`, `429` daily AI cap reached, `502` AI provider failure (invalid output after one repair retry, transport error, or unexpected response shape — the failed call is still recorded in `ai_usage`).

```json
// POST /messages/7/open → 200
{ "status": "opened_in_whatsapp",
  "wa_me_url": "https://wa.me/2348012345678?text=Hello%20..." }
```

### 2.6 Pipeline & follow-ups

| Method | Path | Notes |
|---|---|---|
| GET/PATCH | `/prospects/{id}/stage` | validated transitions only (`409 invalid_transition`) |
| POST | `/prospects/{id}/replies` | `{text, received_at}` → activity + stage `replied` |
| GET/POST | `/prospects/{id}/follow-ups` | |
| POST | `/follow-ups/{fid}/complete` · `/cancel` | |
| GET | `/follow-ups?view=due\|overdue\|upcoming` | dashboard/list queries |
| POST | `/prospects/{id}/do-not-contact` `{reason}` · `/do-not-contact/clear` | terminal guard toggles |

### 2.7 Dashboard & activity

| Method | Path | Returns |
|---|---|---|
| GET | `/metrics` | `DashboardMetrics` (exact fields from `PRODUCT_BRIEF.md` §8) |
| GET | `/prospects/{id}/activities` | append-only timeline (`?page=`) |
| GET | `/activities?since=` | global feed for follow-ups page |

### 2.8 Settings & health

| Method | Path | Returns |
|---|---|---|
| GET | `/settings/providers` | per-provider `{configured: bool, enabled: bool, last_error?}` — **never the key values** |
| GET | `/usage?period=YYYY-MM-DD` | `provider_usage` + `ai_usage` rows |
| PATCH | `/settings` | runtime settings (allowlisted keys only) |
| GET | `/healthz` | `{status:"ok"}` |

---

## 3. Shared schemas (Pydantic, summarized)

```
ProspectCreate   { business_name*, category?, location_area?, phone?, email?,
                   website_url?, social_links?, source_id?, tags? }
Finding          { id, kind*, statement*, source_url?, source_type*, retrieved_at?,
                   verification_status*, confidence*, origin* }
Opportunity      { id, type*, summary*, rationale?, status*, origin*, evidence_finding_ids[] }
OutreachMessage  { id, prospect_id, status*, current_text, origin, grounding[],
                   approved_at?, opened_at?, sent_confirmed_at? }
DashboardMetrics { total_prospects, researched, requires_review, approved_pending_send,
                   sent_confirmed, replies, interested, followups_due, converted }
```

AI structured-output schemas reuse the same Pydantic models (`GeneratedDraft`, `SummarizedResearch`, `OpportunitySuggestion`) with `extra='forbid'` — invalid JSON from a model triggers one repair attempt, then a typed failure (D7).

## 4. Status code policy

| Code | Used for |
|---|---|
| 200/201/202/204 | success / created / accepted (async job) / no content |
| 400 | malformed request (bad CSV, bad URL) |
| 401 | missing/invalid access token **or session cookie** (only when `APP_ACCESS_TOKEN` is set); body `{"detail": …}`. Non-API HTML requests get `302 → /login?next=…` instead |
| 403 | suppressed prospect action; CSRF failure |
| 404 | unknown resource |
| 409 | duplicate prospect; invalid state transition; active-message conflict |
| 413 | upload too large |
| 422 | schema validation (FastAPI default, normalized to error envelope) |
| 429 | provider rate limit surfaced to client (rare; jobs absorb these) |
| 500/502 | unexpected / provider failure — request ID included, no internals leaked |

## 5. Versioning & stability

- `/api/v1` frozen once Phase 4 lands (all MVP endpoints present); breaking changes ⇒ `/api/v2`.
- HTML pages are unversioned (private tool) but keep redirect stubs if URLs change.
- OpenAPI schema served at `/api/docs` (FastAPI default) and exported to `docs/openapi.json` in CI-ish fashion (manual `python -m app.cli export-openapi` in MVP).
