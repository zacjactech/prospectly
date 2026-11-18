# Security and Privacy

Scope: a local-first, single-operator tool that stores personal contact data (names, phones, emails) of third-party businesses and sends manually approved WhatsApp messages. **This document is engineering guidance, not legal advice** — §7 lists items for the owner to confirm (optionally with counsel).

---

## 1. Input validation

- All API bodies validated by Pydantic schemas (`extra='forbid'`, max lengths on every string, URL format checks).
- HTML forms re-validate server-side (never trust the client) with per-field limits: business name ≤200, statement ≤1000, message ≤4096 (WhatsApp's practical limit ~65k, we cap lower for quality).
- CSV upload (secondary Settings utility): ≤2 MB, ≤5,000 rows (over-cap files are rejected with a clear error, never silently truncated), UTF-8/UTF-16 sniff with replacement-char report, per-cell length caps, reject embedded NUL/control chars, max 30 columns.
- Phone numbers validated with `phonenumbers`; invalid numbers stored as `phone_raw` only (never normalized) and flagged.

## 2. Injection & XSS

- **SQLi:** ORM-only access; no string-built SQL anywhere; dynamic filters built from allowlisted column objects; `PRAGMA foreign_keys=ON`.
- **XSS:** Jinja2 autoescaping always on (`{{ }}` only; `|safe` forbidden except for explicitly sanitized fragments — and we don't sanitize rich HTML in MVP at all: all user/AI content renders escaped).
- **CSP:** `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'`. HTMX/vanilla JS served as static files from our origin (no CDNs).
- AI output is text — rendered escaped like any user content.

## 3. CSRF & session (accounts + roles, amended 2026-11-18)

- Session cookie: `HttpOnly`, `SameSite=Strict`, path `/`, `Secure` from `settings.cookie_secure` (`False` for loopback HTTP; set `COOKIE_SECURE=true` behind TLS — closes the old hardcoded-off gap).
- Every state-changing request (forms and JSON) requires a per-session CSRF token (double-submit cookie pattern); enforced by middleware for `POST/PATCH/PUT/DELETE`.

**Access model.** Default bind `127.0.0.1`. `APP_ACCESS_TOKEN` is now only the **gate switch** (plus the non-loopback startup guard): unset ⇒ no auth at all (loopback MVP, unchanged); set ⇒ credentials live in the `users` table.

- **Accounts:** `username` + password hashed with stdlib `hashlib.scrypt` (`n=2**14, r=8, p=1`, 16-byte salt, 32-byte key; self-describing `scrypt$…` store string — no new dependency). Unknown-user logins verify against a dummy hash so timing does not reveal whether a username exists. Password policy: ≥8 chars, not the username, not blank. Usernames: `^[a-z0-9][a-z0-9_.-]{2,39}$`, lowercased, unique.
- **Roles (fail-closed, `app/services/authz.py`):** `owner` (everything, incl. user management) > `admin` (settings + danger zone, no users) > `member` (operational canvas only). Restricted surfaces: `settings`/`danger` → owner+admin, `users` → owner. Prospect visibility: rows with `owner_id` set are visible to their owner (and to admins/owners); `NULL` = shared. Gate off ⇒ checks pass through (no identity exists).
- **Sessions:** opaque cookie value (`secrets.token_urlsafe(32)`, no claims) → `user_sessions` row. Server-side revocation: logout, logout-all, disable-user and password reset all delete rows. 12h **sliding** idle expiry, re-extended on authenticated requests when <1h remains (extended *before* the handler runs — SQLite is single-writer, a post-handler UPDATE deadlocks against the handler's own uncommitted write). Values are never logged.
- **Routes:** `GET/POST /setup` (first run creates the owner while `users` is empty; middleware funnels everything there), `GET/POST /login`, `GET/POST /signup` (env-gated by `ALLOW_SIGNUP`, default off; creates a `member`), `GET/POST /forgot-password` + `GET/POST /reset-password`, `POST /logout`, `POST /logout-all`. Allowlist (no session needed): those pages, `/healthz`, `/static/*`. Unauthenticated API/JSON → `401 {"detail": …}`; HTML → `302 /login?next=<local path>` (`next` accepts local paths only). Permission denials → `403` (HTML error page for browsers, `{"detail": …}` for JSON).
- **Rate limiting (closes the old "no rate limiting" gap):** 5 attempts / 60s per client-IP + subject, in-memory, on login, setup, signup, forgot and reset. Successful login clears the bucket.
- **Forgot-password tradeoff (local-first):** there is no email service, so the reset link is **shown on screen** after submitting a username. This makes usernames enumerable on that page (the response differs only by the link) — mitigated by the rate limit above; single-use SHA-256-hashed token, 30-minute TTL, consumed atomically; a successful reset revokes every session.
- **First run:** with the gate on and an empty `users` table, every path redirects to `/setup`; API callers get JSON `401` (never an HTML page). `POST /logout` clears the cookie **and** deletes the row.
- LAN/public exposure must sit behind TLS with `COOKIE_SECURE=true` (plain-HTTP LAN sends credentials in clear text).


## 4. Secure local configuration & secret handling

- `.env` gitignored; `.env.example` contains placeholder names only. `.gitignore` also excludes `data/*.db*`, `logs/`, `backups/`, `uploads/`.
- Secrets read via pydantic-settings; never logged (redaction filter masks `*_KEY`, `*TOKEN*`, `*SECRET*` patterns), never rendered in HTML (provider page shows "configured" booleans only), never placed in URLs/query strings.
- Startup refuses weak states: LAN bind without `APP_ACCESS_TOKEN` (the token is also the gate on/off switch; the guard lives in the `lifespan` hook in `app/main.py`); `.env` file-permissions guidance on Windows (file ACL note in README). New account env vars: `ALLOW_SIGNUP` (default false), `COOKIE_SECURE` (default false, loopback HTTP).
- No third-party telemetry/analytics of any kind.

## 5. Safe URL fetching (SSRF & abuse) — `SafeFetcher`

All outbound HTTP (research, website checks) passes through one component:

| Control | Value |
|---|---|
| Scheme allowlist | `http`, `https` only (reject `file:`, `ftp:`, `gopher:`, `javascript:`) |
| Host resolution check | Resolve DNS → reject if any address is loopback/private/link-local/CGNAT/reserved (`127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `::1`, `fc00::/7`, `fe80::/10`, `0.0.0.0`, metadata `169.254.169.254`) — checked **per redirect hop** |
| Redirects | max 5; re-validate every hop; log chain |
| DNS rebinding | **accepted residual (not mitigated)** — validation resolves once, httpx re-resolves at connect (TOCTOU window). Hardening path: pin the resolved IP (connect to IP + `Host` header / custom resolver) |
| Timeouts | connect 10s, total 30s |
| Response size | cap 2 MB (abort mid-stream, mark `blocked`) |
| Concurrency | *(not implemented in MVP — fetches are per-request; per-host pacing is future work)* |
| User-Agent | identifiable (`Prospectly/0.1 (+local research tool)`) |
| Robots/ToS posture | fetch only when the user explicitly starts research on a prospect; no crawling loops, no sitemap fan-out; respect `429`/`Retry-After` |
| TLS | verify certificates on (no `verify=False`) |

Additionally: research jobs only target URLs already attached to a prospect (or derived from provider results the operator can see) — no arbitrary server-side URL field that could be used as a proxy.

## 6. File upload & export safety

- Uploads land in `uploads/` with random filenames; original name only used for display; parsed content never re-served as HTML (download/export endpoints send `Content-Disposition: attachment` + `text/csv` with formula-injection escaping: cells starting `= + - @` prefixed with `'`).
- Export features (post-MVP if any) follow the same rule.

## 7. Contact data handling & outreach posture

**Product posture (built-in, not optional):**

| Requirement | How the product implements it |
|---|---|
| Business-purpose contacts | Outreach targets **business contact channels** recorded with source and purpose; `source` + `source_type` stored on every contact point |
| Purpose limitation | Contact fields used only for in-app outreach; no export-to-marketing-tools features |
| Data minimization | We store what the workflow needs (name, business role context, business contact) — no personal (non-business) data collection features |
| Erasure | `DELETE /prospects/{id}?confirm=erase` cascades personal data; tombstone audit without content |
| Opt-out | One-click **Do not contact** (Flow 8); suppression checked before draft/approve/open; suppression list exportable |
| Frequency control | `last_contacted_at` + one-active-message-per-prospect + follow-up pacing defaults; no bulk send path exists |
| Auditability | Append-only `activities` with actor + timestamps |
| Local exposure risk | Main risk is the DB file — mitigated by disk encryption recommendation + backups location guidance (§8) |

**WhatsApp platform rules:** manual click-to-chat keeps a human pressing Send every time (compliant with WhatsApp's stance against unauthorized bulk/automated messaging, [WhatsApp FAQ](https://faq.whatsapp.com/5957850900902049)). Unofficial automation remains permanently out of scope (P3).

**Honest limitation stated in-product:** the tool *prevents* accidental repeated outreach and honors opt-outs; it cannot verify that a public number welcomes contact — the operator's judgment plus frequency caps are part of the outreach posture.

### Open outreach-policy questions for the owner

1. Decide retention period for inactive prospects (recommendation: auto-flag >12 months untouched for review/erasure; owner sets the number).
2. Confirm message-content standards: identify yourself + business purpose in first line (recommended template rule, enforced softly by the draft guidelines).

## 8. Data-at-rest & backups

- DB contains all personal data: recommend enabling OS disk encryption (BitLocker/FileVault) — documented, cannot be enforced by the app.
- Backups are unencrypted copies by default → `backups/` excluded from cloud-sync folders guidance in README; optional `BACKUP_PASSPHRASE` (Phase 5 hardening: encrypt via `age`/`7z` CLI if available — flagged as enhancement).
- File permissions: created with user-only ACLs where the OS allows.

## 9. Abuse-case checklist (tested in QA)

All eight items are automated; names below are the tests that pin them
(last verified: `uv run pytest` 378 passed, 2026-11-17).

- [x] `do_not_contact` prospect: draft generation, approve, open all return 403 with reason. — `tests/test_phase5_security.py::test_do_not_contact_blocks_draft_generate`, `…_blocks_approve_and_open`; round trips in `tests/test_suppression_and_stages.py`
- [x] Skipping states via direct API (`POST /confirm-sent` on `draft`) → 409. — `tests/test_phase3_routes.py::test_illegal_transition_returns_409`, `tests/test_suppression_and_stages.py`
- [x] CSRF token missing → rejected on every mutating endpoint. — `tests/test_csrf.py` (7 tests: missing/wrong multipart token, non-ASCII fails closed)
- [x] `file:///etc/passwd` / `http://169.254.169.254/` / `http://localhost:8000/` as website_url → blocked by SafeFetcher with clear error, check row outcome `blocked`. — `tests/test_phase5_security.py::test_safe_fetcher_blocks_file_scheme`, `…_blocks_private_ip`; per-hop revalidation in `tests/test_safe_fetcher_redirects.py`
- [x] CSV over the size cap, over the 5,000-row cap, or with formula cells → rejected with a 400 (never a truncate, never a 500). — `tests/test_phase5_security.py::test_csv_import_rejects_oversized_file`, `…_rejects_formula_cells`; `tests/test_csv_import.py::test_row_cap_rejected_on_both_surfaces`, `…::test_parse_csv_rejects_rows_over_the_cap`
- [x] XSS payload in business name, finding statement, message text → renders escaped everywhere. — `tests/test_phase5_security.py::test_xss_renders_escaped_in_templates`
- [x] Logs grepped for API keys → zero matches (redaction test). — `tests/test_phase5_security.py::test_log_redaction_masks_api_keys`
- [x] Non-loopback bind without token → process refuses to start. — `tests/test_phase5_security.py::test_non_loopback_bind_without_token_refuses`

## 10. Dependency & supply-chain hygiene

- Minimal dependency set (see README): fastapi, uvicorn, jinja2, sqlalchemy, alembic, httpx, phonenumbers, pydantic-settings, python-multipart, pytest (+ ruff, mypy dev). Each justified in `AGENTS.md` §dependency policy.
- Versions pinned in `uv.lock`; no JS dependencies in MVP. Audit command: `uvx pip-audit` (the `uv pip audit` subcommand does not exist in the installed uv). **Last run: 2026-11-17 → "No known vulnerabilities found".** Re-run at every milestone and before tagging (`docs/RELEASE_CHECKLIST.md` §2), and record the date here or in the release notes.
