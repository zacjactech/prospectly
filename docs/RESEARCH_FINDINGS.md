# Research Findings

Research conducted 2026-11-06 via direct documentation fetches and web search (DuckDuckGo HTML). Every free-tier figure below was verified against multiple sources on that date; **free tiers change frequently — re-verify before relying on any number**. Primary sources are linked inline.

---

## 1. Python backend frameworks

Sources: framework documentation; 2026 comparison articles (mecanik.dev, developersvoice.com, encore.dev, nnaveenkumar.com, dev.to "what I actually use after testing all three in 2026").

| Criterion | FastAPI | Flask | Django |
|---|---|---|---|
| Maintainability for solo dev | Strong: typed routes via Pydantic, auto OpenAPI docs | Minimal structure — you assemble everything yourself | Strong conventions, but heavier than a single-user local tool needs |
| Development speed | Fast for API + Jinja templates | Fastest to start, slower to keep organized at scale | Fastest if you need auth/admin/ORM out of the box |
| Database integration | SQLAlchemy 2.0 / any ORM (you choose) | Any ORM (you choose) | Built-in ORM + migrations |
| Authentication | DIY or plugins (itsdangerous sessions, etc.) | DIY or Flask-Login | Built-in auth system (overkill for single-user MVP) |
| Background jobs | Anyio tasks / separate worker loop; no broker needed for local | Same | Celery/Redis suggested — unnecessary infra for this MVP |
| API support | Native: async, request validation, OpenAPI schema | Via extensions | DRF is powerful but adds a second paradigm |
| Local-first fit | Excellent — one `uvicorn` process | Excellent | Good, but admin/auth machinery unused in MVP |

**Consensus (2026):** FastAPI is the default for new API-oriented projects; Django wins when you need a full product with users; Flask fits the smallest tools.

## 2. Frontend options

Sources: 2026 HTMX vs React analyses (daily.dev, buildmvpfast.com decision framework, abrarqasim.com practitioner write-ups, devtoolswatch.com).

| Option | Deployment | Local dev | Interactive dashboard | Maintainability | Extensibility |
|---|---|---|---|---|---|
| **HTMX + Jinja2 (server-rendered)** | Static files only — no build step | One process, edit template → refresh | Good for CRUD/list/form/inline-edit patterns; weaker for rich canvas/DnD | Very high for solo dev: HTML + a little CSS/JS, no node_modules | Moderate — can add Alpine.js or sprinkle React later on a page-by-page basis |
| React + Vite | Build step, bundle, dev server | Two processes (API + Vite) | Excellent for complex client state | Moderate — you own routing, data fetching, state, forms | High, at the cost of carrying a JS toolchain |
| Next.js | Node server or static export; SSR assumptions | Two processes | Excellent | Lower for this use case: framework conventions (routing, server components) add concepts with no payoff for a localhost tool | High but biased toward hosted deployment |

**Consensus (2026):** hypermedia/HTMX is a strong fit for CRUD apps, dashboards, and internal tools; React is warranted for client-heavy applications (Figma-like complexity). This product is forms, tables, filters, and inline editing — the HTMX profile.

**Verdict:** HTMX + Jinja2 + a small amount of vanilla JS (Alpine.js permitted for local interactivity). No Node build toolchain in the MVP.

## 3. Database

Sources: 2026 SQLite vs PostgreSQL comparisons (tech-insider.org, codewithsql.com, intuitem benchmark, aimadetools.com migration flowchart, raidframe.com indie-SaaS guide).

| Criterion | SQLite | PostgreSQL |
|---|---|---|
| Setup | Zero — one file, no server | Server process + administration |
| Local-first MVP fit | Ideal: WAL mode handles concurrent readers + single writer fine at this scale | Unnecessary for single-user local app |
| Concurrency | Single writer; irrelevant for one user | Multi-writer, MVCC |
| Migration path | Standard SQL + SQLAlchemy; `.dump` → import, or ORM `DATABASE_URL` swap | N/A (start here only if already needed) |
| Future multi-user hosting | Adequate until concurrent writers / multiple app servers | Required then |

**Consensus:** start with SQLite; migrate to PostgreSQL when concurrent multi-writer access or hosted multi-user deployment actually arrives. Keep SQL portable (SQLAlchemy ORM, avoid SQLite-only extensions) so the swap is a connection string + migration run.

**Verdict:** SQLite (WAL) for MVP via SQLAlchemy 2.0 + Alembic migrations; PostgreSQL documented as the deferred (roadmap Phase 6+) target with a written migration procedure.

---

## 4. Business research providers

### 4.1 Business discovery

| Provider | Provides | Free tier (verified 2026-11-06) | Paid | API | Rate limits | Reliability | ToS notes |
|---|---|---|---|---|---|---|---|
| **OpenStreetMap Overpass API** | POIs/shops/businesses by tag + area: name, phone, website, address, coords | Fully free, no key | — | Yes (HTTP query API) | Public instances: per-client concurrent slots + cumulative runtime; HTTP 429 when exceeded; practical guidance ~10k queries/day ([OSM wiki](https://wiki.openstreetmap.org/wiki/Overpass_API), [OSMF API policy](https://operations.osmfoundation.org/policies/api/)) | Good where mappers have covered the area; coverage in Ikorodu is partial — results are a *starting point*, not an authoritative directory | ODbL attribution required; be a polite client (timeouts, `maxsize`, backoff on 429) |
| **Google Places API (New)** Text Search / Place Details | Highest-quality business records: names, categories, phones, websites, addresses, hours | Per-SKU free monthly usage replaced the old $200 credit (March 2025): Essentials SKUs ≈ 10,000 calls/month free, Pro ≈ 5,000, Enterprise ≈ 1,000 ([Google FAQ](https://developers.google.com/maps/billing-and-pricing/faq), [Geoapify analysis](https://www.geoapify.com/is-google-maps-api-free/)) | $5–$8 per 1,000 calls by SKU ([official pricing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing)) | Yes (REST, API key) | Per-method per-project quotas; you must enable **billing** (card) even to use the free allowance | Highest | **Storage rules:** place IDs may be stored (refresh if >12 months old); caching of other place content is restricted; display results on Google Maps or with attribution per policy ([Place ID docs](https://developers.google.com/maps/documentation/places/web-service/place-id)) |
| **Foursquare Places API** | Global POI database (100M+), search by lat/lng + query | 500 free Pro calls/month as of 2026-06-01 (reduced from 10,000) ([Foursquare upcoming-changes](https://docs.foursquare.com/developer/reference/upcoming-changes)) | ~$15 per 1,000 requests | Yes | Monthly free calls, then metered | Good global data; Nigerian coverage weaker than Google | Data license terms apply to storage |
| **Yelp Fusion API** | US-centric business data | No meaningful free tier (reports of ~$229/month starting price) | High | Yes | — | Poor Nigeria coverage | Not applicable for this market |
| **Nigerian directories (ConnectNigeria, BusinessList.com.ng, Hotfrog NG, Vconnect)** | Local listings with contacts | Free to browse | — | **No public API** | n/a (human browsing) | Useful for manual discovery | No published API: MVP treats these as *manual* discovery sources. Programmatic scraping would need a ToS review — deferred, not assumed |
| **Manual add / CSV import** | Anything the user finds | Free | — | n/a | n/a | 100% user-controlled | Primary MVP discovery path; no provider dependency |

### 4.2 Web / search APIs

| Provider | Provides | Free tier (verified 2026-11-06) | Paid | Rate limits | Reliability | ToS notes |
|---|---|---|---|---|---|---|
| **Serper.dev** | Google SERP JSON (web, maps, places) | 2,500 queries, no credit card; reports differ on expiry (some say credits expire in 6 months, some say no monthly reset) — verify at signup ([serper.dev](https://serper.dev), [serpbase.dev](https://serpbase.dev)) | $50 pack ≈ 50k credits ($1/1k); higher tiers cheaper | Prepaid credits | High (fast, stable) | Paid tier required beyond initial batch |
| **Tavily** | AI-oriented web search + page extraction, returns content suitable for grounding | **1,000 credits/month, recurring, no card** ([official docs](https://docs.tavily.com/documentation/api-credits)) | $30/mo → 4,000 credits; PAYG $0.008/credit | Monthly reset | High | Extract credits: 5,000 URL extracts = 1,000 credits worth — cheap grounding source |
| **Brave Search API** | Independent-index web search | Free plan replaced Feb 2026: **$5 free credit/month** (= ~1,000 searches at $5/1k) and requires attribution to Brave on your site ([Brave dashboard](https://api-dashboard.search.brave.com/app/plans), [Implicator analysis](https://www.implicator.ai/brave-drops-free-search-api-tier-puts-all-developers-on-metered-billing/)) | $5/1,000 searches, plans from there | Per-plan RPM | High | Attribution requirement is a real constraint for an internal tool |
| **ddgs (DuckDuckGo library, formerly `duckduckgo-search`)** | Keyless DDG result scraping | Free | — | Unpublished; bot-detection `202 Ratelimit` errors are common ([BrowserFoundry analysis](https://www.browserfoundry.cloud/blog/duckduckgo-search-api)) | Flaky by design — no SLA, breaks when DDG changes pages | Unofficial; ToS risk. Acceptable only as a *best-effort fallback* with graceful failure, never a hard dependency |
| **SerpAPI** | Google SERP | 100 searches/month (widely cited; verify at signup) | From ~$75/mo | Monthly | High | Costly beyond hobby volume |

### 4.3 Website availability & technical inspection

| Approach | Provides | Cost | Notes |
|---|---|---|---|
| **Direct HTTP fetch (httpx)** | DNS result, HTTP status, redirects, TLS, response time, final HTML, `<title>`, meta, links, forms, generator tags | Free | MVP core. Must include SSRF protections (see `SECURITY_AND_PRIVACY.md`): scheme allowlist, private-IP block, redirect limits, timeouts, size caps |
| **HTML heuristics (our own rules)** | "No contact form/booking path visible", "services not in HTML", "site is a single unstyled page", tech hints (WordPress generator, etc.) | Free | Sufficient for MVP opportunity signals; keeps us independent of paid tech-intel APIs |
| **BuiltWith** | Technology lookup database | Free API is limited to the suffix list / basic lookups; full lookup API is paid ([BuiltWith free API](https://api.builtwith.com/free-api)) | Not needed for MVP |
| **Wappalyzer** | Tech detection API | Paid plans only ([pricing](https://www.wappalyzer.com/pricing/)) | Not needed for MVP |
| **Lighthouse (headless Chrome)** | Performance/accessibility scores | Free but heavy (Chromium, ~GBs, slow) | Deferred post-MVP; only if "site quality score" proves necessary |

**Key principle:** "website does not exist" is a *positive finding with evidence* (searches run, DNS NXDOMAIN, HTTP failure modes recorded); "could not verify" is an *incomplete check* (timeout, block, bot-challenge). The data model must store these differently (`PRODUCT_BRIEF.md` P6).

### 4.4 Social media presence

| Approach | Provides | Cost / limits | Verdict |
|---|---|---|---|
| **Meta Graph API (Facebook/Instagram)** | Page/profile lookup | No generic "find business by name/location" endpoint for arbitrary use; long-lived tokens + app review required for business discovery use cases; rate-limited | **Not used in MVP** — cannot power discovery without app review |
| **Search-engine results** (`site:instagram.com "business name"`) via Tavily/Serper | Evidence that a social profile exists + profile URL | Consumes the same search credits as §4.2 | **MVP path:** one search query per research run, stored as `findings` with `kind='social'`, `source_type='search_result'` — evidence links, not profile data |
| **Manual inspection** | Profile existence, follower count, last-post recency, link-in-bio presence | Free (operator's browser) | Operator records a `kind='social'` finding with `source_type='user'` — always allowed |
| Third-party social APIs (Apify, Brand24, etc.) | Aggregated profile data | Paid, ToS-sensitive | Excluded — no MVP feature needs it |

**Result:** social presence is a *finding kind*, not a provider integration. Profiles themselves are stored only as URLs on the prospect (`social_links`, manual entry per `PRODUCT_BRIEF.md` §6 exclusions).

---

## 5. AI providers

Sources: official rate-limit pages and 2026 free-tier trackers (ai.google.dev, console.groq.com, klymentiev.com, costbench.com, apidog.com).

| Provider | Models for our tasks | Free tier (verified 2026-11-06) | Paid pricing | Structured output support | Notes |
|---|---|---|---|---|---|
| **Google Gemini API** | Flash-class models strong at summarization/structured extraction | Free tier exists without card: RPM/RPD/TPM caps per model (e.g., Flash models in the tens of RPM, ~1–2k requests/day class limits) ([official rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)) | Pay-as-you-go very cheap for Flash models; tiers unlock at $10/$1,000 spend | Yes — native JSON/response schema | Best free-tier quality-to-cost ratio for structured research tasks |
| **Groq** | Open models (Llama, Qwen, GPT-OSS class) | Free, no card: ≈ 30 requests/min, ~1,000 requests/day, ~8k tokens/min per org on current free models ([rate limits](https://console.groq.com/docs/rate-limits), [tracker](https://klymentiev.com/blog/groq-api-free-tier)) | Cheap pay-as-you-go after | JSON mode supported | Very fast; daily caps are the binding constraint |
| **OpenRouter** | One key → many models incl. `:free` variants | Free models: ≈ 20 req/min, 50 req/day on new accounts; 1,000/day after $10 lifetime spend ([tracker](https://klymentiev.com/blog/openrouter-free-tier)) | Pay-as-you-go + 5.5% fee on paid routes | Depends on model; JSON mode common | **Best provider-flexibility story:** swap models/providers without touching business logic |
| **Ollama (local)** | Small open models (7–8B class) | Free, offline, no data leaves machine | Hardware cost only | Model-dependent; weaker JSON reliability on small models | Viable fallback for draft *assist* on capable hardware; not required |
| **OpenAI / Anthropic / etc.** | Frontier quality | No free tier of consequence | $ per token | Yes | Paid fallback behind same abstraction |

**What genuinely requires AI vs. what does not:**

| Works without AI | Genuinely better with AI |
|---|---|
| Prospect CRUD, CSV import, dedupe | Research summarization from fetched pages/search results |
| Website fetch + status classification (rule-based) | Opportunity analysis phrased in natural language with rationale |
| Rule-based opportunity heuristics (no site, unreachable, no contact link) | Personalized first-line of the outreach message grounded in findings |
| Draft from user-written templates with `{{placeholders}}` | Draft quality when findings are heterogeneous |
| Follow-ups, statuses, dashboard counts | Contradiction detection across findings (nice-to-have) |

**Failure handling requirements (derived):** timeouts (≤60s), retries with exponential backoff + jitter on 429/5xx (max 3), circuit-breaker style disable after repeated failures, token-usage logging per call, JSON-schema validation of outputs with one repair attempt, and graceful degradation to template-based drafts when AI is unavailable.

**Provider abstraction requirement:** business logic must depend only on a `ResearchSummarizer`, `OpportunityAnalyzer`, `MessageGenerator` interface; concrete adapters (`gemini`, `groq`, `openrouter`, `ollama`, `template`) selected by config.

---

## 6. WhatsApp

### 6.1 Click-to-chat (chosen for MVP)

- Format: `https://wa.me/<E.164 digits>?text=<url-encoded message>` (equivalent long form `api.whatsapp.com/send?phone=...&text=...`) ([official WhatsApp FAQ](https://faq.whatsapp.com/425247423114725)).
- Free, no approval, no API key, works for personal and WhatsApp Business numbers.
- Opens the chat with the message **prefilled in the composer**; the human presses Send.
- Cannot detect whether the message was sent — which is exactly why the product requires explicit "I sent it" confirmation (P2/P4).

### 6.2 Official WhatsApp Business Platform (Cloud API) — contrast

- Server-to-server Meta API: requires approved WhatsApp Business Account, message templates pre-approved by Meta for business-initiated (marketing/utility) conversations, and per-message/per-conversation billing ([Meta pricing docs](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/)).
- Pricing effective Oct 2026 is per delivered template message by category and market; Nigerian marketing templates ≈ $0.05/message class rates (third-party tabulation — verify on Meta's rate card before any use).
- Designed for customer-service scale and notification templates — **not** for one-to-one human-approved personal outreach at MVP volume.
- Revisit only if volume ever justifies template approval workflows and per-message cost (documented in `COST_ANALYSIS.md` scalable tier).

### 6.3 Explicitly rejected approaches

- **Unofficial automation** (Baileys, whatsapp-web.js, Selenium/Playwright driving WhatsApp Web): violates WhatsApp ToS; Meta detects modified clients and bans numbers — losing the number loses the channel ([Meta on unauthorized automation](https://faq.whatsapp.com/5957850900902049), [ban analyses](https://www.pipes.bot/blog/baileys-vs-whatsapp-cloud-api-ban)).
- **Bulk-sender tools:** the product principle P1 forbids them regardless of technology.

### 6.4 MVP decision

Normalize phone → E.164 → build `wa.me` URL → open via OS default handler (`webbrowser`/`start`) → user sends manually → user clicks "I sent this message" → only then `sent_confirmed` + activity log entry. The app never claims delivery it cannot observe.

---

## 7. Compliance research (summary — full treatment in `SECURITY_AND_PRIVACY.md`)

- **Nigeria Data Protection Act (NDPA) 2023** ([official PDF](https://ndpc.gov.ng/wp-content/uploads/2024/03/Nigeria_Data_Protection_Act_2023.pdf)): processing personal data requires a lawful basis (consent or legitimate interest among others); purpose limitation; data-subject rights (access, erasure, objection); breach notification duties; registration duties for higher-volume controllers. **Public availability of a phone number or email does not equal marketing consent.**
- **NDPC guidance** on consent, commercial permission, and opt-out management applies to outreach ([NDPC consent guidance coverage](https://4notify.net/ng/en/federal-gazette/ndpc-ndpa-2023-consent)).
- **WhatsApp spam rules:** unauthorized bulk/automated messaging is an enforcement target ([WhatsApp FAQ](https://faq.whatsapp.com/5957850900902049)); our manual click-to-chat flow keeps a human in the loop for every message.
- Practical posture adopted: rely on **legitimate-interest-style B2B outreach only with** (a) relevant business contact details used for their intended business purpose, (b) strict frequency caps, (c) immediate opt-out honoring, (d) do-not-contact suppression, (e) auditable activity log. This is documented product behavior, **not legal advice** — flagged for user confirmation.

---

## 8. Research conclusions (feeding `TECHNOLOGY_DECISIONS.md`)

1. Backend: FastAPI; Frontend: HTMX + Jinja2 (no build step); DB: SQLite → PostgreSQL path.
2. Discovery: manual/CSV + Overpass as the free core; Google Places as an optional billing-gated provider; Nigerian directories as manual sources.
3. Search: Tavily (recurring free 1,000 credits) primary free search; Serper for one-off larger batches; ddgs only as best-effort fallback.
4. Website inspection: our own httpx-based checker; no paid tech-intel APIs in MVP.
5. AI: interface-first; Gemini free tier default, Groq/OpenRouter alternates, template mode as no-AI floor.
6. WhatsApp: click-to-chat only; Cloud API deferred; unofficial automation permanently out.
