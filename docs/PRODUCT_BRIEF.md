# Product Brief — Prospectly

**Status:** Phase 0 complete (application scaffold running); build proceeds per `IMPLEMENTATION_ROADMAP.md`.
**Owner:** Solo developer (user).
**Scope:** Offer-agnostic and location-agnostic — any prospects, any market; the platform is for finding clients and running cold and hot outreach.

---

## 1. Problem statement

Finding new clients through word-of-mouth, referrals, and manual outreach does not scale. Identifying the right prospects — real organizations worth contacting, with an evidence-backed reason to reach out — takes hours of manual searching, verifying, and messaging, and the process is easy to abandon because it is unstructured and hard to track.

Existing CRMs are built for sales teams, are expensive, assume email-first pipelines, and do not handle the specific workflow of: discover → research → verify → identify opportunity → draft personalized WhatsApp message → human approval → manual send → track reply/follow-up → convert.

## 2. Product vision

A lightweight, local-first, semi-automated prospecting and outreach platform that:

1. Helps discover potential clients in a target market/category.
2. Stores them as prospects with deduplication and source tracking.
3. Researches each prospect from publicly available information, retaining evidence (source URL, timestamp, verification status).
4. Distinguishes verified facts from uncertain findings and AI-generated hypotheses.
5. Identifies specific, evidence-backed outreach opportunities (website/web-presence signals are the initial rule set — one signal type among others).
6. Generates a personalized WhatsApp draft grounded in that research.
7. Requires **mandatory manual approval** before any outbound message.
8. Opens WhatsApp with the approved message prefilled (click-to-chat) — never sends it.
9. Records "sent" only after explicit user confirmation.
10. Tracks replies, follow-ups, opt-outs, and conversions.

## 3. Core workflow (the product's spine)

```
Discover → Add → Research → Verify → Analyze opportunity → Draft →
Review/Edit → Approve → Open WhatsApp → User sends → Confirm sent →
Record reply → Follow up → Convert / Not interested / Do not contact
```

Every screen and data model in this product serves one of these steps.

## 4. Non-negotiable principles

| # | Principle | Engineering consequence |
|---|-----------|------------------------|
| P1 | Semi-automated, not a bulk bot | No batch-send capability anywhere in the codebase; single-message actions only |
| P2 | Human approval before every outbound message | Status machine: `draft → pending_review → approved → opened_in_whatsapp → sent_confirmed`; `sent_confirmed` only via explicit user action |
| P3 | Never auto-send WhatsApp | The system only builds a URL; it never posts to WhatsApp |
| P4 | Opening WhatsApp ≠ sent | `opened_in_whatsapp` is a separate state from `sent_confirmed` |
| P5 | Never invent business facts | AI output is labeled by origin (`human` / `provider` / `ai`); AI findings default to `unverified` confidence `low` |
| P6 | Distinguish "no website" from "could not verify" | `website_status` enum: `no_website_found` vs `check_failed` vs `reachable` vs `unreachable`; never conflate |
| P7 | Evidence retention | Every significant finding stores source URL, source type, retrieval timestamp, verification status, confidence |
| P8 | Research is editable/reviewable | Findings and drafts are user-editable; edits are logged |
| P9 | No duplicates, no repeated outreach | Normalized-key dedupe at entry; outreach guards block re-contact of `sent_confirmed` / `do_not_contact` prospects |
| P10 | Opt-out / do-not-contact | `do_not_contact` flag + suppression list checked before every draft/send action |
| P11 | Free/low-cost first | Paid services only behind feature flags with documented justification |
| P12 | Solo-developer maintainable | One language, one process, one database file, no microservices, no queue broker |

## 5. Users

Single primary user: **the owner** — a solo operator using the platform to find clients and run outreach. The application is a personal operational tool, not a multi-tenant SaaS in the MVP. Architecture must not preclude a later hosted, multi-user version (see `TECHNOLOGY_DECISIONS.md`, database strategy).

## 6. MVP scope

### In scope (MVP)

- **Prospect management:** manual add, CSV import, edit, archive, tags, notes, source tracking, duplicate detection (fuzzy name + normalized phone/email/website).
- **Business research:** run a research job per prospect that fetches website status, collects findings from configured providers, and stores evidence records; all findings editable.
- **Opportunity analysis:** rule-based heuristics over findings (initial rule set: no website found, unreachable site, no visible contact path; rules are extensible to any outreach-relevant signal) + optional AI analysis, each opportunity linked to supporting findings.
- **Personalized outreach:** AI-generated or template-based WhatsApp draft, grounded in approved findings; full editing; mandatory approval gate.
- **WhatsApp handoff:** E.164 phone normalization, `wa.me` URL construction, "Open WhatsApp" action, then explicit "I sent it" confirmation.
- **Outreach management:** full status lifecycle + append-only activity history.
- **Follow-ups:** due dates, overdue view, reply recording, not-interested / do-not-contact actions.
- **Dashboard:** operational metrics only (see §8).
- **Settings:** provider configuration (keys via environment), feature flags for optional paid providers.

### Explicitly out of scope (MVP)

- Any automatic sending (WhatsApp, email, SMS).
- Browser automation of WhatsApp Web or any unofficial WhatsApp library.
- Multi-user accounts, roles, hosted deployment, billing.
- Email/SMS channels (architecture keeps a channel boundary for later).
- Social media APIs (Facebook/Instagram/LinkedIn) — manual link entry only in MVP.
- Payment tracking/invoicing (conversion is a status, not an invoicing system).
- Mobile app, map visualization, lead scoring ML, A/B testing, analytics vanity charts.
- Lighthouse-grade performance audits (basic HTTP/HTML inspection only).

### Deferred candidates (post-MVP, in priority order)

1. Email channel behind the same approval gate.
2. Google Places discovery provider (requires billing account — see `COST_ANALYSIS.md`).
3. Browser-assisted research (Playwright) for JS-heavy sites.
4. Hosted/Postgres multi-user version.
5. Official WhatsApp Business Platform integration (only if volume justifies per-message costs).

## 7. Success criteria for the MVP

- A user can go from "business name typed in" to "approved WhatsApp message open in WhatsApp" for one prospect without leaving the app (except WhatsApp itself).
- Zero messages can be recorded as sent without explicit confirmation.
- Every AI-generated claim in a draft can be traced to a finding with a source URL (or is visibly marked unverified).
- Duplicate prospects are blocked or flagged at import/add time.
- `do_not_contact` prospects cannot receive drafts or outreach actions.
- The whole system runs on one machine with `uvicorn` + one SQLite file and zero mandatory paid services.

## 8. Dashboard metrics (operational, not vanity)

| Metric | Definition |
|---|---|
| Total prospects | Count of non-archived prospects |
| Researched | Prospects with ≥1 completed research run |
| Requires review | Prospects with unverified/contradicted findings or pending drafts |
| Approved messages | Drafts in `approved` (not yet opened) state |
| Sent (confirmed) | Messages in `sent_confirmed` |
| Replies | Prospects with ≥1 inbound reply activity |
| Interested | Prospects in `interested` stage |
| Follow-ups due | Open follow-ups with `due_date ≤ today` |
| Converted | Prospects in `converted` stage |

Explicitly excluded: open rates, "AI-generated" counts, page views, chart clutter.

## 9. Terminology

- **Prospect** — a potential client (business or contact organization) worth approaching.
- **Finding** — an evidence-backed research fact/observation with a source.
- **Opportunity** — an evidence-backed reason to reach out (e.g., a web-presence gap or missing contact path), linked to supporting findings.
- **Outreach message** — a WhatsApp draft bound to one prospect, versioned.
- **Stage** — prospect-level pipeline position (replied, interested, converted, …).
- **Message status** — lifecycle of a single outreach message (draft … sent_confirmed).
- **Suppression** — the set of prospects that must never be contacted (opt-out, do-not-contact, invalid number).

## 10. Related documents

- `RESEARCH_FINDINGS.md` — provider research with sources and limits.
- `TECHNOLOGY_DECISIONS.md` — stack comparison and rationale.
- `SYSTEM_ARCHITECTURE.md` — components and boundaries.
- `DATABASE_DESIGN.md`, `API_DESIGN.md`, `USER_FLOWS.md`, `UI_UX_SPECIFICATION.md`.
- `SECURITY_AND_PRIVACY.md`, `COST_ANALYSIS.md`, `IMPLEMENTATION_ROADMAP.md`.
