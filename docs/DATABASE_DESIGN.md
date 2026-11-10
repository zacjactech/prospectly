# Database Design

**Engine:** SQLite (WAL) for MVP · SQLAlchemy 2.0 ORM · Alembic migrations · PostgreSQL-ready (D4).
**Conventions:** `id` = integer PK (SQLite) / `BIGSERIAL` (PG); all timestamps **UTC, timezone-aware**; soft delete via `archived_at` / `suppressed_at` where noted; enums stored as TEXT with application-level `Enum` classes (portable across SQLite/PG); `JSON` columns via SQLAlchemy `JSON` type.

---

## 1. Entity-relationship overview

```
sources ─┬─< prospects >─< tags (M:N via prospect_tags)
         │       │
         │       ├─< notes
         │       ├─< findings ──── (source_url, evidence)
         │       ├─< website_checks
         │       ├─< opportunities >─< opportunity_findings >─ findings
         │       ├─< outreach_messages ──< message_versions (implicit: versions are rows)
         │       ├─< follow_ups
         │       ├─< activities (append-only)
         │       └─< suppression_entries (1:1 flag lives on prospect; list view unions)
         ├─< research_runs ──< jobs (1:1 per execution)
provider_usage · ai_usage · jobs · settings (global tables)
```

---

## 2. Tables

### 2.1 `sources`
Where a prospect came from (P: source tracking).

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| type | TEXT enum | `manual`, `csv_import`, `osm_overpass`, `google_places`, `foursquare`, `web_search`, `directory_browse`, `referral`, `other` |
| label | TEXT | e.g. "Industry directory – hotels" |
| detail | JSON | import filename, query used, provider response page |
| created_at | DATETIME | |

### 2.2 `prospects`

**Dedupe keys (P9):** unique partial indexes where `archived_at IS NULL`:
- `uq_prospects_norm_name` on `(normalized_name)`
- `uq_prospects_phone` on `(phone_e164)` (when not null)
- `uq_prospects_email` on `(LOWER(email))` (when not null)
- `uq_prospects_domain` on extracted domain of `website_url` (when not null)

Add/import flow: candidate keys are checked → exact match blocks with "existing prospect" link; fuzzy match (trigram/Jaro on name + same city) → **flag for review**, never silently merge. Merging is a manual action (Phase 2 nicety: `merge_into` service that re-links children and logs the merge).

### 2.3 `tags` / `prospect_tags`
`tags(id, name UNIQUE, color?)` · `prospect_tags(prospect_id, tag_id)` PK `(prospect_id, tag_id)`.

### 2.4 `notes`
`id, prospect_id FK, body TEXT, created_at, updated_at, edited_from` (notes are editable, history via `activities`).

### 2.5 `research_runs`
| Column | Notes |
|---|---|
| id | |
| prospect_id FK | |
| status | `queued`, `running`, `succeeded`, `partially_completed`, `failed` |
| steps | JSON — per-step status (`{website_check: "ok", search: "failed: rate_limited", …}`) |
| error | TEXT NULL |
| started_at / finished_at | |
| created_at | |
| triggered_by | `user`, `scheduled`, `retry` |

### 2.6 `findings` — the evidence core (P7)

| Column | Notes |
|---|---|
| id | |
| prospect_id FK | |
| research_run_id FK NULL | NULL for manually entered findings |
| kind | `identity`, `category`, `location`, `services`, `customers`, `website`, `website_quality`, `social`, `contact`, `observation` |
| statement | TEXT — the claim, one sentence |
| detail | TEXT NULL — elaboration |
| source_url | TEXT NULL — required unless `source_type='user'` |
| source_type | `website_fetch`, `search_result`, `directory_listing`, `social_profile`, `osm_overpass`, `bizdata`, `places_api`, `ai_inference`, `user` |
| retrieved_at | DATETIME — when the evidence was seen |
| recorded_at | DATETIME — when we stored it |
| verification_status | `verified`, `unverified`, `contradicted`, `not_found` |
| confidence | `high`, `medium`, `low` |
| origin | `human`, `provider`, `ai` (P5) |
| finding_key | TEXT — stable hash of `(kind, normalized statement)` for idempotent re-import |
| edited_by / original_statement | audit of human edits to AI findings |

**Unique:** `(prospect_id, finding_key)`.
**Invariant:** `origin='ai'` ⇒ initial `verification_status='unverified'` AND `confidence='low'`; only a human edit/action may raise them (service-enforced).

### 2.7 `website_checks`
| Column | Notes |
|---|---|
| id, prospect_id FK, run_id FK NULL | |
| url_checked | |
| outcome | `reachable`, `unreachable_http`, `dns_failure`, `timeout`, `blocked`, `redirect_loop`, `invalid_url`, `not_attempted` |
| http_status | INT NULL |
| final_url | after redirects |
| response_ms | |
| title | `<title>` if any |
| has_contact_path | BOOLEAN — form/`mailto:`/`tel:`/WhatsApp link detected in HTML |
| has_booking_or_cta | BOOLEAN |
| signals | JSON — generator meta, tech hints, word count, language hints |
| error | TEXT NULL |
| checked_at | |

`prospects.website_status` is derived from the latest check + search evidence. Rule (amended 2026-11-10, owner-approved): `no_website_found` only when a **successful** search found nothing AND (the URL's DNS fails OR no URL is recorded — there is nothing to DNS-check, so search-empty is the evidence; USER_FLOWS Flow 2 step 6 "AND/OR"). `check_failed` for `timeout`/`blocked`/HTTP failures and for failed or absent searches — "could not verify", never "no website" (P6). `reachable` when the latest check is reachable.

### 2.8 `opportunities` + `opportunity_findings`
| opportunities | Notes |
|---|---|
| id, prospect_id FK | |
| type | `no_standalone_website`, `website_inaccessible`, `hard_to_find_info`, `unclear_services`, `no_inquiry_path`, `weak_listings_presentations`, `other` |
| summary | one line shown in UI |
| rationale | longer explanation of *why this matters for this business* |
| status | `proposed`, `validated`, `dismissed` |
| origin | `rule`, `ai`, `human` |
| created_at, updated_at | |

`opportunity_findings(opportunity_id, finding_id)` — evidence links (P: "explain the evidence behind every opportunity").
**Guard:** an opportunity of type `no_standalone_website` may only exist if ≥1 linked finding with `verification_status IN ('verified','unverified')` — never auto-validated on AI-only evidence.

### 2.9 `outreach_messages`
| Column | Notes |
|---|---|
| id, prospect_id FK | |
| status | `draft`, `pending_review`, `approved`, `opened_in_whatsapp`, `sent_confirmed` |
| active | BOOLEAN — one active message per prospect (partial unique index) |
| current_text | final text (edited copy) |
| generated_text | original generation output (for diff/audit) |
| origin | `ai`, `template`, `human` |
| generator_meta | JSON — provider, model, prompt version, token usage id |
| grounding | JSON — list of finding IDs used at generation time |
| ungrounded_acknowledgement | BOOLEAN — set when user approved without full grounding |
| approved_at, opened_at, sent_confirmed_at | DATETIME NULL |
| created_at, updated_at | |

**Status transitions enforced in service layer** (see `SYSTEM_ARCHITECTURE.md` §3.1). `sent_confirmed` writes `activities` and sets `prospects.last_contacted_at`, `stage='contacted'`.

### 2.10 `follow_ups`
`id, prospect_id FK, message_id FK NULL, due_date DATE, note TEXT, status ('open','done','cancelled'), completed_at, created_at`.
Index: `(status, due_date)` for the dashboard query.

### 2.11 `activities` — append-only history (P: complete activity history)
`id, prospect_id FK, type, actor ('user','system','ai'), payload JSON, created_at`.

Types: `created`, `imported`, `edited`, `archived`, `unarchived`, `merged`, `research_started`, `research_finished`, `finding_added`, `finding_verified`, `finding_edited`, `opportunity_created`, `opportunity_validated`, `opportunity_dismissed`, `draft_created`, `draft_edited`, `draft_submitted`, `draft_approved`, `whatsapp_opened`, `message_sent_confirmed`, `message_not_sent`, `reply_received`, `stage_changed`, `follow_up_set`, `follow_up_completed`, `do_not_contact_set`, `do_not_contact_cleared`, `note_added`, `tag_added`.

Never updated or deleted (no `updated_at`).

### 2.12 `jobs` (D5)
`id, type ('research','followup_sweep','provider_probe'), payload JSON, status ('queued','running','succeeded','failed','cancelled'), run_after DATETIME, attempts INT, max_attempts default 3, locked_at, last_error, created_at, updated_at`.
Index: `(status, run_after)`.

### 2.13 `provider_usage` / `ai_usage`
- `provider_usage(id, provider, kind ('discover','search','inspect'), calls INT, errors INT, rate_limited INT, period DATE, updated_at)` — UNIQUE `(provider, kind, period)` for token-bucket/daily-cap accounting.
- `ai_usage(id, provider, model, task ('summarize','analyze','generate'), prompt_tokens, completion_tokens, calls, ok_calls, failed_calls, period DATE)` — UNIQUE `(provider, model, task, period)`.

### 2.14 `settings`
`key TEXT PK, value JSON, updated_at` — runtime toggles (category vocabulary, default follow-up days, daily caps). Secrets are **not** stored here (env only, D8).

### 2.15 `profiles` — user profile (single owner, one row)
Drives automated discovery (who to look for, where) and personalizes outreach drafts (who you are, what you sell).

| Column | Notes |
|---|---|
| id | PK |
| offer | TEXT — what you sell / your value proposition |
| target_industries | JSON array — industries/categories to target |
| target_locations | JSON array — locations to search |
| ideal_signals | JSON — signals that mark a good prospect (e.g. no website, outdated site, no contact path) |
| updated_at | DATETIME |

### 2.16 `discovery_jobs` — automated discovery runs keyed to profile
| Column | Notes |
|---|---|
| id | PK |
| profile_id FK → profiles | |
| status | `queued`, `running`, `succeeded`, `partially_completed`, `failed` |
| query | TEXT — discovery query derived from profile |
| candidates_found | INT — candidates surfaced |
| prospects_created | INT — prospects created after operator review |
| error | TEXT NULL |
| started_at / finished_at | |
| created_at | |

### 2.12 `jobs` (D5)
`id, type ('research','discover','followup_sweep','provider_probe'), payload JSON, status ('queued','running','succeeded','failed','cancelled'), run_after DATETIME, attempts INT, max_attempts default 3, locked_at, last_error, created_at, updated_at`.

---

## 3. Relationship summary

| Relationship | Cardinality | Delete behavior |
|---|---|---|
| sources → prospects | 1 : N | PROTECT (archive instead) |
| prospects → notes/findings/runs/checks/opportunities/messages/follow_ups/activities | 1 : N | CASCADE (history goes with prospect — see retention note) |
| research_runs → findings | 1 : N | SET NULL on run deletion (manual runs keep evidence) |
| opportunities ↔ findings | M : N | CASCADE join rows |
| prospects ↔ tags | M : N | CASCADE join rows |
| outreach_messages → activities | via payload reference | — |

**Retention note:** deletion of a prospect is allowed only via an explicit "delete personal data" action, which cascades findings/activities and writes a tombstone `settings` audit entry (what was deleted, when, by whom) containing no personal data.

## 4. Indexes (query-driven)

| Query | Index |
|---|---|
| Prospect list default sort / stage filter | `(archived_at, stage, updated_at)` |
| Dashboard counts | partial indexes on `stage`, `do_not_contact` |
| Due follow-ups | `follow_ups(status, due_date)` |
| Dedupe lookups | partial uniques in §2.2 |
| Job claiming | `jobs(status, run_after)` |
| Findings by prospect | `findings(prospect_id, kind)` |
| Activity timeline | `activities(prospect_id, created_at)` |
| Message guard | partial unique `outreach_messages(prospect_id) WHERE active = 1` |

## 5. Migration strategy

1. **MVP:** Alembic scripts generated from ORM metadata; `alembic upgrade head` on startup command (manual step documented in README, not automatic).
2. **SQLite → PostgreSQL (Phase 6+, deferred):** (a) freeze writes; (b) `alembic upgrade head` against PG (scripts written dialect-portable); (c) export data via ORM-level dump script (not `sqlite3 .dump` — avoids type mismatches); (d) swap `DATABASE_URL`; (e) run smoke suite. Procedure gets a dedicated checklist in `IMPLEMENTATION_ROADMAP.md` Phase 6.
3. Reserved column: `prospects.owner_id` (already present, unused) — avoids a painful later migration.

## 6. Backup

- Command: `python -m app.cli backup` → uses SQLite `VACUUM INTO` to copy a consistent snapshot to `backups/prospectly-YYYYMMDD.db` (kept last N).
- Recommended cadence: before each import batch and weekly (documented in README).
