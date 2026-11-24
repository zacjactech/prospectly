# API Design

Two surfaces over one service layer (D3):

1. **Pages** (`/`) — one server-rendered **Pipeline Canvas** (`docs/design/pipeline-canvas.md` §V2) + HTMX partials for the human UI.
2. **JSON API** (`/api/v1`) — programmatic contract for tests, automation, and any future UI.

Conventions (both surfaces): JSON API uses standard HTTP status codes; timestamps ISO-8601 UTC; errors as `{ "error": { "code", "message", "details?" } }` (FastAPI `HTTPException` paths — including the access gate's `401 {"detail": …}` — return bare `{"detail": …}`); list endpoints support `?page=&page_size=&sort=&filter...`; **all state-changing JSON endpoints require header `X-CSRF-Token`** (from cookie) — session auth is cookie-based: when `APP_ACCESS_TOKEN` is set (the gate switch), `GET/POST /login` authenticates a `users` account and issues a revocable DB-backed session (see `SECURITY_AND_PRIVACY.md` §3).

**Role policy (Phase B, both surfaces).** With the gate on, three route-level
dependencies enforce `authz.PERMISSIONS` — `settings` (owner+admin), `danger`
(owner+admin), `users` (owner only) — and return `403` with the copy in
`authz.PERMISSION_MESSAGES`. Everything else is authenticated-only. Row
visibility is separate from role: a `member` only ever sees `owner_id IS NULL`
(shared) or `owner_id = self` prospects, enforced by `AuthMiddleware` for
direct row URLs and by `scope_criterion` inside list/facet/dashboard/outreach
queries. With the gate unset (`APP_ACCESS_TOKEN` absent) every permission
dependency passes through and `owner_id` is never stamped.

---

## 1. Pages (HTML)

**Two families of routes (spec §V2.2, amended 2026-11-18).** Canvas routes
(`/`, `/prospects`, `/outreach`) render the `canvas/shell.html` world —
setup cluster, stage board, outreach region; retired views render standalone
tab-based pages (settings tab page, detail tab page, generic standalone) with
no canvas behind them. Every route still has two renderings:

- **Plain GET** → the full document: canvas shell (canvas routes, `/` with the
  Status card open) or the standalone page (retired views).
- **`HX-Request: true`** → the body fragment only (what the canvas Filters/Add
  panels load over HTMX; also kept for fragment parity on page routes).

The nine setup cards (`/status`, `/settings/profile`, `/settings/providers`,
`/settings/usage`, `/settings/general`, `/settings/import`,
`/settings/saved-views`, `/settings/retention`, `/merge/duplicates`) each open
a numbered mini-pipeline panel; the routes are unchanged, only the surface
moved. Retired page templates are gone; `login.html` keeps its own chrome.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | **Canvas** with the **Status** card open (attention queue · metrics · quick start). Gate on + **signed out** → the **landing page** instead: wordmark, one line of copy, Sign in (plus *Create an account* when `ALLOW_SIGNUP=true`), rendered by `AuthMiddleware` before any route so it carries **no data at all**. Signed in → the canvas with the **Status** card open, the world hidden behind it until the panel closes (§V2 amendment 2026-11-19). Gate unset, first run (`users` empty → `302 /setup`), a dead cookie (`302 /login?reason=…`) or signed in → unchanged |
| GET | `/status` | Status card alone (rail item + `g s` chord) |
| GET | `/prospects` | Canvas (filters: stage, tag, category, location, search, include-archived) — the default landing surface; `HX-Request` → board fragment only |
| GET | `/prospects/filters` | 360px Filters panel body (same filter fields + saved views); `HX-Request` → panel fragment only. Stage filter renders a single-zone lens |
| GET/POST | `/prospects/new` | Manual add — opens as a canvas panel |
| GET/POST | `/settings/import` · `/settings/import/commit` | CSV upload + preview + confirm, as a card panel **(secondary utility; not part of discovery flow)** |
| GET | `/prospects/{id}` | Detail card panel (Overview · Research · Opportunities · Outreach · Activity) |
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
| GET | `/settings/providers` (HTML) · `/api/v1/settings/providers` (JSON) | Provider status card: configured/enabled/usage/cap/last failure — **never the key values** (§2.8) |
| GET | `/settings/usage`, `/settings/general`, `/settings/import`, `/settings/retention`, `/settings/profile` (POST `/settings/profile`) | Setup cards (usage, general settings, CSV wizard, retention, profile). No `/settings` index — the canvas setup cluster *is* the index |
| POST | `/settings/profile/discovery/run` | Queue profile-driven discovery (Flow 1: `discovery_job` + `jobs` row; processed by the worker) |
| GET/POST | `/settings/saved-views` · POST `/{id}/delete` | Saved views card — filters whitelisted to the list query keys (unknown keys → 400) |
| GET | `/prospects/views/{id}/apply` | Redirect to `/prospects?` with the view's filters |
| GET | `/outreach` | Canvas framed on the outreach region (messages in flight); `HX-Request` → outreach region fragment only |
| GET | `/healthz` | Liveness (no auth, no data) |
| GET | `/dev/styleguide` | Hidden design-system reference (`dev/styleguide.html`): every token and `.ds-*` component state. Unlinked from nav, `include_in_schema=False`, uses the normal auth gate; no data access, no HTMX fragment. Loads `styleguide.css` only on this page |

**Auth pages** (own router, standalone dark chrome, all CSRF-protected POSTs;
reachable without a session — the allowlist):

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/setup` | First run (empty `users`): create the **owner** account, then sign in. GET redirects to `/login` once any account exists; the gate funnels every path here while none do |
| GET/POST | `/login` | Sign in (username + password) → issues the session cookie; `?next=<local path>` honoured after login, `?reason=expired\|disabled\|reset` renders the matching copy; already-signed-in GET → `303 /` |
| GET/POST | `/signup` | Open registration → creates a `member` (only when `ALLOW_SIGNUP=true`, else `303 /login`) |
| GET/POST | `/forgot-password` | Request a reset link — local-first: the single-use link is rendered on the page (rate limited; enumeration tradeoff documented in SECURITY §3) |
| GET/POST | `/reset-password` | Consume `?token=`, set a new password, revoke every session → `303 /login?reason=reset` |
| POST | `/logout` | Revoke this session row + clear the cookie → `303 /login` |
| POST | `/logout-all` | Revoke every session of the signed-in user → `303 /login` (Account page) |

**Account & Users pages** (Phase B; session required, not allowlisted):

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/settings/account` | any signed-in role | Own account: username, role badge, active sessions (`this device` marker), change-password form. Gate off → `303 /` (no identities exist) |
| POST | `/settings/account/password` | any signed-in role | Verify current password (rate limited 5/60s per IP+username → plain-text `429`), set the new one, then revoke **every other** session of that user (this device keeps working) → `303 /settings/account` |
| GET | `/settings/users` | `users` (owner) | User management table + create form |
| POST | `/settings/users` | `users` (owner) | Create an account (`username`, `password`, `confirm`, `role` ∈ `owner\|admin\|member`) → `303 ?flash=…`; `400` re-render on policy/duplicate errors |
| POST | `/settings/users/{id}/role` | `users` (owner) | Change role → `303 ?flash=…`; `404 User not found`; self-lockout refusals arrive as `303 ?flash=…&flash_type=error` |
| POST | `/settings/users/{id}/toggle` | `users` (owner) | Disable (revokes that user's sessions immediately) / re-enable → `303` |
| POST | `/settings/users/{id}/delete` | `users` (owner) | Delete an account; its prospects' `owner_id → NULL` (become shared) → `303` |

Self-lockout guards (`_last_owner_guard`) refuse with a `303` + error flash:
you can never disable or delete yourself, and the last **active** owner can
never be disabled, deleted, or demoted (a disabled owner does not count).

Errors on these pages re-render the form with `error` copy (200/401/403/429);
credential endpoints are rate limited (5 attempts / 60s per IP+subject).

HTMX partials return HTML fragments (`HX-Request` header honored); full page
loads return either the canvas shell (canvas routes + action panels) or the
standalone page (retired views). Flash/toast messages via `HX-Trigger`
headers.

---

## 2. JSON API `/api/v1`

### 2.1 Prospects

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/prospects` | `stage`, `tag`, `category`, `location`, `q`, `archived`, `page`, `page_size` | `ProspectList` |
| POST | `/prospects` | `ProspectCreate` | `201 Prospect` · `409 duplicate` · `422 validation` |
| GET | `/prospects/{id}` | — | `Prospect` (with counts) — `404` for a row outside a member's scope |
| PATCH | `/prospects/{id}` | `ProspectPatch` | `200 Prospect` · `409 duplicate` (identity keys re-checked) |
| DELETE | `/prospects/{id}` | `?confirm=erase` | `204` (explicit erase path, cascades) — **not built**; see QA checklist §Known gaps #3 |
| POST | `/prospects/{id}/archive` · `/unarchive` | — | `200 Prospect` |
| POST | `/prospects/{id}/merge` | `{target_prospect_id}` | `200 Prospect` (re-links children, logs) |

**Duplicate response example** (bare `{"detail": …}`, same shape as every other
`HTTPException` path):

```json
{ "detail": "A prospect with this phone number already exists." }
```

The copy is field-level and leak-free. Only a caller who can *see* the
conflicting row ever gets the row-named variant
(`Duplicate prospect already exists: {name} (id={n})` / on edit
`Would duplicate existing prospect: {name} (id={n})`) — an unscoped caller
(owner/admin, or the gate off) always can, a scoped `member` never does, so
the two paths are indistinguishable and cannot be used as an existence
oracle. The scoped pre-check and the partial-unique-index backstop therefore
return byte-identical messages.

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
| GET | `/settings/providers` | per-provider `{configured: bool, enabled: bool, last_error?}` — **never the key values**; requires `settings` (owner+admin) when the gate is on |
| GET | `/usage?period=YYYY-MM-DD` | `provider_usage` + `ai_usage` rows — **not built**: usage is HTML-only at `GET /settings/usage` (QA checklist §Known gaps) |
| PATCH | `/settings` | runtime settings (allowlisted keys only) — **not built** (QA checklist §Known gaps) |
| GET | `/healthz` | `{status:"ok"}` — allowlisted, no session needed |

The other gated JSON endpoints in this file — `POST /imports/preview`,
`POST /imports/commit`, `GET /merge/duplicates` (`settings`) and `POST /merge`,
`POST /retention/sweep`, `POST /retention/{id}/clear` (`danger`, owner+admin) —
are documented where they sit in the flow; the HTML-only `/settings/users*`
set (`users`, owner only) is in §1.

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
| 401 | no/invalid session cookie or wrong credentials (only when `APP_ACCESS_TOKEN` is set); body `{"detail": …}` for API, login form for HTML. Non-API HTML requests get `302 → /login?next=…` instead |
| 403 | suppressed prospect action; CSRF failure; role denial (`require_permission` for `settings`/`danger`/`users` — HTML error page "Not allowed" for browsers, `{"detail": …}` for JSON); disabled account; login/setup password-policy violations render the form with 401/403 |
| 404 | unknown resource **or a row the caller may not see**: for a `member`, `AuthMiddleware._path_denial` answers `404` with the *same* body the route would return for a missing id (`Prospect not found`, `Message not found`, …), so hidden and absent rows are byte-identical — there is no existence oracle. Applies to `/prospects/{id}`, `/findings/{id}`, `/notes/{id}`, `/messages/{id}`, `/opportunities/{id}`, `/follow-ups/{id}`, `/research/runs/{id}` and their `/api/v1` equivalents. `/retention/{id}/clear` is deliberately **not** in that table (it is `danger`-gated, so adding it would only create a 404-vs-403 oracle) |
| 409 | duplicate prospect; invalid state transition; active-message conflict. For a scoped (`member`) caller the duplicate copy is always the leak-free `A prospect with this {field} already exists.` — never the row's name/id — because the scoped pre-check and the unique-index backstop must be indistinguishable |
| 413 | upload too large |
| 422 | schema validation (FastAPI default, normalized to error envelope) |
| 429 | provider rate limit surfaced to client (rare; jobs absorb these); credential-endpoint rate limit (login/setup/signup/forgot/reset **and change-password**, 5/60s per IP+subject — the change-password bucket is keyed `ip:account:{username}` so a hijacked session cannot brute-force the current password) |
| 500/502 | unexpected / provider failure — request ID included, no internals leaked |

## 5. Versioning & stability

- `/api/v1` frozen once Phase 4 lands (all MVP endpoints present); breaking changes ⇒ `/api/v2`.
- HTML pages are unversioned (private tool) but keep redirect stubs if URLs change.
- OpenAPI JSON served at `/api/openapi.json`; human docs at `/api/docs` (Swagger UI) and `/api/redoc`, rendered from pinned self-hosted bundles under a docs-only CSP exception (no CDN). Schema export remains manual: `python -m app.cli export-openapi` → `docs/openapi.json` in MVP.
