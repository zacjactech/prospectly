# Cost Analysis

Three operating configurations. **All figures verified 2026-11-06** (see `RESEARCH_FINDINGS.md` for sources) — free tiers change; re-verify before depending on any number. The MVP is designed to be fully usable in Configuration 1.

---

## Configuration 1 — Free (default MVP target): **$0/month**

Everything required for the complete workflow works without payment. *Caveat: some "free" tiers require signup; one requires a card (Google) — excluded here to keep the promise honest.*

| Capability | Free solution | Limits to respect |
|---|---|---|
| App runtime | Own machine + Python + SQLite | — |
| Prospect discovery | Manual + CSV import + **OpenStreetMap Overpass** (no key) | OSM coverage is partial in many areas — treat as lead *hints*; public-instance rate limits (~10k queries/day practical guidance, 429s possible) |
| Business research search | **Tavily** — 1,000 credits/month recurring, no card | ~200–1,000 searches depending on depth/extract mix; budget ≤10 searches/prospect |
| Search fallback | `ddgs` library (keyless) | Unofficial, flaky (202 ratelimit), ToS risk — *fallback only, product never depends on it* |
| Website inspection | Own httpx checker | Free, unlimited (subject to polite-use §5 of security doc) |
| AI: summaries, opportunities, drafts | **Gemini free tier** (no card) *or* **Groq free** (≈30 rpm / ≈1k req/day) *or* **OpenRouter `:free` models** (≈20 rpm / 50 req/day new accounts) | RPM/RPD caps: comfortable for ≤30 AI calls/day; structured-output repair retries count against caps |
| AI floor with zero keys | `template` adapter — placeholder drafts from findings | Manual editing required (by design) |
| WhatsApp outreach | Click-to-chat (`wa.me`) | Free, unlimited, human-sent |
| Auth/backup/monitoring | Loopback bind, file copy backups, local logs | — |
| **Total** | | **$0** |

**Free-tier monthly budget example (heavy month):** 400 prospects × 5 Tavily searches = 2,000 credits → exceeds free tier → reduce to 2 searches/prospect + manual browsing, or move to Configuration 2. The app's Usage page tracks this.

## Configuration 2 — Low cost (when free limits bite): **≈ $5–30/month**

Trigger-based: adopt a line only when its free tier actually blocks work.

| Item | Cost | When to add |
|---|---|---|
| **Google Places API (New)** — best discovery data quality (global coverage) | ~$0 within Essentials free allowance (**≈10,000 calls/month free per SKU**) then ~$5–8/1,000; **requires billing account/card** | When OSM coverage blocks discovery; 1,000 prospects/month of Text Search fits inside free allowance for most months |
| **Serper** search top-up | $50 prepaid ≈ 50k queries (6-month expiry per reports) ≈ $1/1,000 | When Tavily's 1,000 credits run out mid-month |
| **Tavily Project plan** | $30/mo → 4,000 credits | If search becomes daily-heavy |
| **Gemini paid tier** | Pennies per 1,000 Flash-class tokens; spend caps configurable | If free RPM/RPD limits slow research runs |
| Groq/OpenRouter top-ups | Pay-as-you-go (OpenRouter adds ~5.5% fee on paid routes) | Alternative to Gemini if one provider degrades |
| **Total realistic** | | **≈ $0–30/mo**; typical steady state **$5–15/mo** |

**Justification rule (P11):** a paid item is added only when (a) a documented free tier is exhausted in normal use, (b) it unlocks a workflow step that manual work cannot reasonably cover, and (c) the cost is recorded here. Paid providers stay behind config flags.

## Configuration 3 — Scalable (future hosted / multi-user product)

Only if the tool becomes a shared product — **not needed for the current objective**.

| Item | Indicative cost | Notes |
|---|---|---|
| VPS (2 vCPU / 4 GB) | $5–12/mo | Runs app + PostgreSQL |
| PostgreSQL | $0 (self-hosted) or managed $15+/mo | Phase 6+ migration path documented |
| Domain + TLS | $0–15/yr | |
| Auth/multi-user hardening | $0 (engineering time) | |
| **WhatsApp Business Platform** (if ever used) | Per delivered template message: marketing ≈ **$0.05/msg** class rates (market-dependent; third-party tabulation, Oct 2026 Meta rate card — verify before use); utility ≈ $0.007/msg class | Only for approved-template outreach at volume; **replaces nothing in the approval workflow** — approval gate stays |
| Backups/monitoring | $0–5/mo | |
| **Total** | | **≈ $25–60/mo** + per-message WhatsApp costs |

## What works without AI (feature cost map)

| Without any AI key | Needs AI for good UX | Needs a paid service ever? |
|---|---|---|
| All prospect CRUD, import, dedupe | Research summarization across many sources | No — not for any MVP feature |
| Website status + rule-based opportunities | Natural-language opportunity rationales | Google Places only *if* you want automated discovery at scale (free allowance may suffice) |
| Template drafts with placeholders | Personalized first lines at quality | Search top-ups only if Tavily free tier exhausts |
| Everything about follow-ups, statuses, dashboard, approval, WhatsApp | | WhatsApp: never (click-to-chat is free) |

## Cost-control engineering (built in)

- Per-provider daily caps in settings (defaults conservative) + persisted usage counters → hard stop before surprise bills.
- Google Places adapter (post-MVP) requests minimal field masks; place-ID-only refreshes where possible (free) per Google's own guidance.
- AI calls log tokens per task; Usage page shows estimated spend for paid tiers.
- No paid infrastructure in MVP: one process, one file database, no broker, no CDN.

## Summary

| Configuration | Monthly cost | Unlocks |
|---|---|---|
| 1 — Free | **$0** | Entire MVP workflow end-to-end |
| 2 — Low cost | **$5–30** | Better discovery (Places), more search/AI headroom |
| 3 — Scalable | **$25–60+** (+WhatsApp per-message) | Hosted multi-user product |
