# Prospectly

A lightweight, local-first, **semi-automated** prospecting and outreach platform for finding clients and running **cold and hot** WhatsApp outreach — offer- and location-agnostic.

Discover prospects → research them with evidence → identify outreach opportunities → draft a personalized WhatsApp message → **approve it yourself** → open WhatsApp and send it manually → track replies, follow-ups, and conversions.

Default path: **configure your profile first** (what you sell, who you target, where), then run automated discovery jobs to find prospects — no CSV required to start.

> **Status: Phase 5 complete — hardening & release candidate.** Requirements, research, and architecture decisions live in [`docs/`](docs/); work proceeds phase-by-phase against the exit criteria in `docs/IMPLEMENTATION_ROADMAP.md`.

## Product principles (non-negotiable)

- **No bulk messaging.** Human approval is mandatory before every outbound message.
- **The app never sends WhatsApp.** It builds a prefilled [click-to-chat](https://faq.whatsapp.com/425247423114725) link (`wa.me/...`); you press Send.
- **"Opened" ≠ "Sent."** A message is recorded as sent only after you explicitly confirm it.
- **No invented facts.** AI output is labeled, starts unverified, and is traceable to sources.
- **"No website found" ≠ "could not verify."** These are stored differently.
- **Evidence everywhere:** findings keep source URL, type, timestamp, verification status, confidence.
- **Free/low-cost first:** the full MVP workflow costs **$0/month** (see `docs/COST_ANALYSIS.md`).

Full list: `docs/PRODUCT_BRIEF.md` §4.

## Documentation map

| Document | Contents |
|---|---|
| [docs/PRODUCT_BRIEF.md](docs/PRODUCT_BRIEF.md) | Vision, workflow, principles, MVP scope |
| [docs/RESEARCH_FINDINGS.md](docs/RESEARCH_FINDINGS.md) | Provider research with sources, free tiers, limits, ToS notes |
| [docs/TECHNOLOGY_DECISIONS.md](docs/TECHNOLOGY_DECISIONS.md) | Stack comparisons and binding decisions (D1–D14) |
| [docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md) | Components, boundaries, state machines, jobs |
| [docs/DATABASE_DESIGN.md](docs/DATABASE_DESIGN.md) | Schema, relationships, indexes, migration path |
| [docs/API_DESIGN.md](docs/API_DESIGN.md) | Pages + JSON API contracts |
| [docs/USER_FLOWS.md](docs/USER_FLOWS.md) | The ten core journeys and their guards |
| [docs/UI_UX_SPECIFICATION.md](docs/UI_UX_SPECIFICATION.md) | Navigation, pages, components, tokens, responsive/a11y |
| [docs/SECURITY_AND_PRIVACY.md](docs/SECURITY_AND_PRIVACY.md) | Validation, SSRF, CSRF, outreach posture, checklist |
| [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) | Phases with testable exit criteria |
| [docs/COST_ANALYSIS.md](docs/COST_ANALYSIS.md) | Free / low-cost / scalable configurations |
| [AGENTS.md](AGENTS.md) | Engineering instructions for AI agents & contributors |

## Recommended stack (summary)

- **Backend:** Python 3.12+ (developed on 3.15.2) · FastAPI · SQLAlchemy 2.0 · Alembic · Jinja2 + HTMX (no JS build step)
- **Database:** SQLite (WAL) → PostgreSQL when hosted multi-user ever happens
- **Providers (all optional, behind adapters):** OSM Overpass (discovery, free) · Tavily (search, 1,000 free credits/mo) · own httpx site inspector · Gemini/Groq/OpenRouter (AI) or the built-in **template mode** (works with zero keys)
- **WhatsApp:** official `wa.me` click-to-chat only

Details and trade-offs: `docs/TECHNOLOGY_DECISIONS.md`.

## Setup

> Verified end-to-end on this machine (2026-11-06): fresh copy → `.env` → sync → migrate → tests → running app in ~1 minute. Machine quirks (broken `winget`, hanging `python` alias) are documented in `docs/TECHNOLOGY_DECISIONS.md` D14.

```powershell
# 1. Prerequisites (once per machine): Python 3.12+ and uv
#    Python: python.org "Python Install Manager"  (winget is broken on this machine)
#    uv:     py -m pip install uv                 # run below as `py -m uv` — no PATH changes needed

# 2. Enter the project
cd Prospectly

# 3. Environment
Copy-Item .env.example .env                # then fill in optional provider keys

# 4. Virtualenv, dependencies & database
py -m uv sync --python "$env:LOCALAPPDATA\Programs\Python\Python315\python.exe"
#    ↑ explicit interpreter on purpose: the `python` app-execution alias hangs,
#      so uv must not search PATH for one (D14). Subsequent uv commands reuse .venv.
py -m uv run alembic upgrade head

# 5. Run
py -m uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
```

### Quality gate (runs before every milestone)

```powershell
py -m uv run ruff check .
py -m uv run ruff format --check .
py -m uv run mypy app
py -m uv run pytest
```

### Configuration

All keys are documented in `.env.example`. Highlights:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | defaults to local SQLite file |
| `BIND_HOST` / `APP_ACCESS_TOKEN` | loopback by default; token **required** if bound beyond loopback |
| `TAVILY_API_KEY` / `SERPER_API_KEY` / … | optional search providers (feature-flagged by presence) |
| `AI_PROVIDER` | `gemini` \| `groq` \| `openrouter` \| `ollama` \| `template` (default floor: `template`) |
| `*_DAILY_CAP` | hard usage caps per provider |
| `PHONE_DEFAULT_COUNTRY` | ISO country code applied to national-format numbers during E.164 normalization (unset = international format required) |

**User profile** (in Settings): what you offer, target industries/categories, target locations, ideal-client signals — drives automated discovery jobs and personalizes outreach drafts. This is where you configure discovery before adding prospects.

**Secrets never go in git.** `.env` is ignored; commit only `.env.example`.

## Backup

```powershell
py -m uv run python -m app.cli backup    # consistent snapshot to backups/ (Phase 0+)
```

Back up before major operations (import batches, bulk edits) and weekly. Keep `backups/` out of cloud-sync folders (it contains personal data).

## License / data note

Private project. It stores third-party business contact details — handle the database file as personal data (`docs/SECURITY_AND_PRIVACY.md`).
