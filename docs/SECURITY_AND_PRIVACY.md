# Security and Privacy

Scope: a local-first, single-operator tool that stores personal contact data (names, phones, emails) of third-party businesses and sends manually approved WhatsApp messages. **This document is engineering guidance, not legal advice** — §7 lists items for the owner to confirm (optionally with counsel).

---

## 1. Input validation

- All API bodies validated by Pydantic schemas (`extra='forbid'`, max lengths on every string, URL format checks).
- HTML forms re-validate server-side (never trust the client) with per-field limits: business name ≤200, statement ≤1000, message ≤4096 (WhatsApp's practical limit ~65k, we cap lower for quality).
- CSV upload: ≤2 MB, ≤5,000 rows, UTF-8/UTF-16 sniff with replacement-char report, per-cell length caps, reject embedded NUL/control chars, max 30 columns.
- Phone numbers validated with `phonenumbers`; invalid numbers stored as `phone_raw` only (never normalized) and flagged.

## 2. Injection & XSS

- **SQLi:** ORM-only access; no string-built SQL anywhere; dynamic filters built from allowlisted column objects; `PRAGMA foreign_keys=ON`.
- **XSS:** Jinja2 autoescaping always on (`{{ }}` only; `|safe` forbidden except for explicitly sanitized fragments — and we don't sanitize rich HTML in MVP at all: all user/AI content renders escaped).
- **CSP:** `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'`. HTMX/vanilla JS served as static files from our origin (no CDNs).
- AI output is text — rendered escaped like any user content.

## 3. CSRF & session

- Session cookie: `HttpOnly`, `SameSite=Strict`, `Secure` off only for pure-localhost HTTP (documented), path `/`.
- Every state-changing request (forms and JSON) requires a per-session CSRF token (double-submit cookie pattern); enforced by middleware for `POST/PATCH/PUT/DELETE`.
- **Access model:** default bind `127.0.0.1` (no auth needed for loopback). Optional `APP_ACCESS_TOKEN` env: if set, a login gate issues the session — required when binding beyond loopback (`BIND_HOST` ≠ 127.0.0.1 refuses to start without a token).
- Session expiry: 12h idle timeout; token rotation on login.

## 4. Secure local configuration & secret handling

- `.env` gitignored; `.env.example` contains placeholder names only. `.gitignore` also excludes `data/*.db*`, `logs/`, `backups/`, `uploads/`.
- Secrets read via pydantic-settings; never logged (redaction filter masks `*_KEY`, `*TOKEN*`, `*SECRET*` patterns), never rendered in HTML (provider page shows "configured" booleans only), never placed in URLs/query strings.
- Startup refuses weak states: missing `.env` permissions guidance on Windows (file ACL note in README), LAN bind without token, `DEBUG=true` with non-loopback bind.
- No third-party telemetry/analytics of any kind.

## 5. Safe URL fetching (SSRF & abuse) — `SafeFetcher`

All outbound HTTP (research, website checks) passes through one component:

| Control | Value |
|---|---|
| Scheme allowlist | `http`, `https` only (reject `file:`, `ftp:`, `gopher:`, `javascript:`) |
| Host resolution check | Resolve DNS → reject if any address is loopback/private/link-local/CGNAT/reserved (`127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `::1`, `fc00::/7`, `fe80::/10`, `0.0.0.0`, metadata `169.254.169.254`) — checked **per redirect hop** |
| Redirects | max 5; re-validate every hop; log chain |
| DNS rebinding | pin resolved IP for the connection (connect to IP + `Host` header / custom resolver) |
| Timeouts | connect 10s, total 30s |
| Response size | cap 2 MB (abort mid-stream, mark `blocked`) |
| Concurrency | ≤5 in-flight fetches; polite delay; per-host pacing |
| User-Agent | identifiable (`Prospectly/0.1 (+local research tool)`) |
| Robots/ToS posture | fetch only when the user explicitly starts research on a prospect; no crawling loops, no sitemap fan-out; respect `429`/`Retry-After` |
| TLS | verify certificates on (no `verify=False`) |

Additionally: research jobs only target URLs already attached to a prospect (or derived from provider results the operator can see) — no arbitrary server-side URL field that could be used as a proxy.

## 6. File upload & export safety

- Uploads land in `uploads/` with random filenames; original name only used for display; parsed content never re-served as HTML (download/export endpoints send `Content-Disposition: attachment` + `text/csv` with formula-injection escaping: cells starting `= + - @` prefixed with `'`).
- Export features (post-MVP if any) follow the same rule.

## 7. Contact data handling, consent & outreach compliance (NDPA 2023)

Framework: Nigeria Data Protection Act 2023 ([official text](https://ndpc.gov.ng/wp-content/uploads/2024/03/Nigeria_Data_Protection_Act_2023.pdf)) + NDPC guidance on consent/commercial outreach.

**Product posture (built-in, not optional):**

| Requirement | How the product implements it |
|---|---|
| Lawful basis | B2B outreach to **business contact channels** recorded with source and purpose; we store `source` + `source_type` on every contact point and treat legitimate-interest-style B2B outreach as the working basis — **owner to confirm this characterization** (§9 Q1) |
| Purpose limitation | Contact fields used only for in-app outreach; no export-to-marketing-tools features |
| Data minimization | We store what the workflow needs (name, business role context, business contact) — no personal (non-business) data collection features |
| Right to erasure | `DELETE /prospects/{id}?confirm=erase` cascades personal data; tombstone audit without content |
| Right to object / opt-out | One-click **Do not contact** (Flow 8); suppression checked before draft/approve/open; suppression list exportable |
| Frequency control | `last_contacted_at` + one-active-message-per-prospect + follow-up pacing defaults; no bulk send path exists |
| Auditability | Append-only `activities` with actor + timestamps |
| Breach duty | Local app: main risk is the DB file — mitigated by disk encryption recommendation + backups location guidance (§8) |

**WhatsApp platform rules:** manual click-to-chat keeps a human pressing Send every time (compliant with WhatsApp's stance against unauthorized bulk/automated messaging, [WhatsApp FAQ](https://faq.whatsapp.com/5957850900902049)). Unofficial automation remains permanently out of scope (P3).

**Honest limitation stated in-product:** the tool *prevents* accidental repeated outreach and honors opt-outs; it cannot verify that a public number welcomes marketing — the operator's judgment plus frequency caps are part of the compliance posture.

### Open compliance questions for the owner (must confirm before launch)

1. Confirm lawful-basis characterization for B2B WhatsApp outreach (legitimate interest vs consent) — ideally with a Nigerian data-protection practitioner.
2. Confirm whether NDPC controller registration obligations apply at this volume.
3. Decide retention period for inactive prospects (recommendation: auto-flag >12 months untouched for review/erasure; owner sets the number).
4. Confirm message-content standards: identify yourself + business purpose in first line (recommended template rule, enforced softly by the draft guidelines).

## 8. Data-at-rest & backups

- DB contains all personal data: recommend enabling OS disk encryption (BitLocker/FileVault) — documented, cannot be enforced by the app.
- Backups are unencrypted copies by default → `backups/` excluded from cloud-sync folders guidance in README; optional `BACKUP_PASSPHRASE` (Phase 5 hardening: encrypt via `age`/`7z` CLI if available — flagged as enhancement).
- File permissions: created with user-only ACLs where the OS allows.

## 9. Abuse-case checklist (tested in QA)

- [ ] `do_not_contact` prospect: draft generation, approve, open all return 403 with reason.
- [ ] Skipping states via direct API (`POST /confirm-sent` on `draft`) → 409.
- [ ] CSRF token missing → rejected on every mutating endpoint.
- [ ] `file:///etc/passwd` / `http://169.254.169.254/` / `http://localhost:8000/` as website_url → blocked by SafeFetcher with clear error, check row outcome `blocked`.
- [ ] CSV with 10k rows / 10 MB / formula cells → rejected/escaped.
- [ ] XSS payload in business name, finding statement, message text → renders escaped everywhere.
- [ ] Logs grepped for API keys → zero matches (redaction test).
- [ ] Non-loopback bind without token → process refuses to start.

## 10. Dependency & supply-chain hygiene

- Minimal dependency set (see README): fastapi, uvicorn, jinja2, sqlalchemy, alembic, httpx, phonenumbers, pydantic-settings, python-multipart, pytest (+ ruff, mypy dev). Each justified in `AGENTS.md` §dependency policy.
- Pin exact versions in `requirements.txt` (or `uv.lock`); `pip-audit`/`uv pip audit` run at Phase 0 and each milestone; no JS dependencies in MVP.
