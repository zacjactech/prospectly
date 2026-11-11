"""Provider status for Settings → Providers (API_DESIGN §1 and §2.8).

This is the Phase 2 exit criterion *"Settings → Providers status page"*: one
read-only view of every provider the app can use — whether it is configured,
whether it will actually be used, how much it has been used today, whether
its daily cap has tripped, and the most recent failure it recorded.

**Key values are never returned** — only booleans about their presence
(SECURITY_AND_PRIVACY §4, D8). The Brave row is honest about a config
variable that has no adapter behind it, and DDGS is labelled as the
conditional fallback it is.

Nothing here mutates state: enable/disable and "test call" affordances are
deliberately out of scope (see UI_UX_SPECIFICATION §2.7, noted as deferred).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.ai import get_generator
from app.config import settings
from app.models import AIUsage, ResearchRun
from app.providers.factory import get_discovery_provider, get_search_provider
from app.services.usage import (
    ai_calls_today,
    ai_cap_reached,
    daily_cap_for,
    provider_calls_today,
    provider_cap_reached,
)


@dataclass(frozen=True)
class ProviderSpec:
    """Static description of one provider row."""

    key: str  # settings / provider_configured key
    label: str
    role: str  # search | discovery | fetch | ai | floor
    implemented: bool = True
    key_attr: str | None = None  # settings attribute holding the API key
    usage_name: str | None = None  # provider_usage.provider value
    step: str | None = None  # research_runs.steps key for failure lookup
    cap_kind: str | None = None  # "provider" | "ai" | None
    note: str | None = None


# Order = display order on the page.
SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "tavily",
        "Tavily",
        "search",
        key_attr="tavily_api_key",
        usage_name="tavily",
        step="search",
        cap_kind="provider",
    ),
    ProviderSpec(
        "serper",
        "Serper",
        "search",
        key_attr="serper_api_key",
        usage_name="serper",
        step="search",
        cap_kind="provider",
    ),
    ProviderSpec(
        "brave",
        "Brave Search",
        "search",
        implemented=False,
        key_attr="brave_api_key",
        usage_name="brave",
        step="search",
        note="config only — no adapter is wired into the factory",
    ),
    ProviderSpec(
        "ddgs",
        "DuckDuckGo (HTML)",
        "search",
        usage_name="ddgs",
        step="search",
        note="fallback — used only when no keyed search provider is set",
    ),
    ProviderSpec(
        "bizdata",
        "BizData",
        "discovery",
        usage_name="bizdata",
        step="discovery",
        note="keyless; preferred discovery source when enabled",
    ),
    ProviderSpec(
        "osm",
        "OpenStreetMap Overpass",
        "discovery",
        usage_name="osm_overpass",
        step="discovery",
        note="keyless; used when BizData is off",
    ),
    ProviderSpec(
        "http_fetch",
        "Website fetch (SafeFetcher)",
        "fetch",
        usage_name="http_fetch",
        step="website_check",
        note="own fetcher — not an external provider, no cap",
    ),
    ProviderSpec(
        "gemini",
        "Gemini",
        "ai",
        key_attr="gemini_api_key",
        note="active when AI_PROVIDER=gemini",
        cap_kind="ai",
    ),
    ProviderSpec(
        "groq",
        "Groq",
        "ai",
        key_attr="groq_api_key",
        note="active when AI_PROVIDER=groq",
        cap_kind="ai",
    ),
    ProviderSpec(
        "openrouter",
        "OpenRouter",
        "ai",
        key_attr="openrouter_api_key",
        note="active when AI_PROVIDER=openrouter",
        cap_kind="ai",
    ),
    ProviderSpec(
        "ollama",
        "Ollama (local)",
        "ai",
        note="local service; active when AI_PROVIDER=ollama",
        cap_kind="ai",
    ),
    ProviderSpec(
        "template",
        "Template floor",
        "floor",
        note="zero-key, zero-network drafts (P11) — never capped; "
        "active when no AI provider is selected",
    ),
)

AI_USAGE_STEP = "draft"


def _recent_failures(db: Session, *, limit: int = 200) -> dict[str, str]:
    """Newest recorded failure per research step, e.g. ``search → "failed: …"``."""
    runs = db.query(ResearchRun).order_by(ResearchRun.id.desc()).limit(limit).all()
    found: dict[str, str] = {}
    for run in runs:
        steps = run.steps or {}
        for step, value in steps.items():
            if step in found or not isinstance(value, str):
                continue
            if value.startswith("failed"):
                found[step] = value.removeprefix("failed:").strip()
    return found


def _ai_failure_note(db: Session) -> str | None:
    rows = db.query(AIUsage).filter(AIUsage.period == date.today(), AIUsage.failed_calls > 0).all()
    failed = sum(row.failed_calls for row in rows)
    return f"{failed} failed call(s) today" if failed else None


def _active_search_name() -> str | None:
    """Provider name the search factory would use right now (no network)."""
    try:
        provider = get_search_provider()
    except Exception:  # pragma: no cover - defensive: config misread
        return None
    return getattr(provider, "provider_name", None) if provider else None


def _active_discovery_name() -> str | None:
    try:
        provider = get_discovery_provider()
    except Exception:  # pragma: no cover - defensive
        return None
    return getattr(provider, "provider_name", None) if provider else None


def _active_ai_name() -> str | None:
    """Generator the AI factory would return right now (no network)."""
    try:
        return getattr(get_generator(), "provider_name", None)
    except Exception:  # pragma: no cover - defensive
        return None


def provider_status(db: Session) -> list[dict[str, object]]:
    """Per-provider status rows for the Providers page and its JSON twin.

    Never includes a key value — only ``configured`` / ``enabled`` booleans,
    counters and cap state.

    ``enabled`` means **will actually be used**: it is decided by asking the
    same factories the services use, so the page cannot drift from runtime
    behaviour (e.g. Serper is configured but not enabled while a Tavily key
    is present, and a discovery failure belongs to whichever source the
    factory picks — not to both).
    """
    failures = _recent_failures(db)
    ai_failure = _ai_failure_note(db)
    active_search = _active_search_name()
    active_discovery = _active_discovery_name()
    active_ai = _active_ai_name()

    rows: list[dict[str, object]] = []
    for spec in SPECS:
        configured = bool(getattr(settings, spec.key_attr)) if spec.key_attr else True

        if spec.role == "search":
            enabled = spec.implemented and spec.usage_name == active_search
        elif spec.role == "discovery":
            enabled = spec.implemented and spec.usage_name == active_discovery
        elif spec.role == "ai":
            enabled = spec.implemented and spec.key == active_ai
        elif spec.role == "floor":
            enabled = active_ai == "template"
        else:  # fetch — always on, it is our own code
            enabled = spec.implemented

        cap: int | None  # None = unmetered (no declared cap)
        if spec.role == "ai":
            used = ai_calls_today(db)
            cap = int(settings.ai_daily_cap)
            reached = ai_cap_reached(db)
            last_error = ai_failure if enabled else None
        elif spec.usage_name:
            used = provider_calls_today(db, spec.usage_name)
            cap = daily_cap_for(spec.usage_name)
            reached = provider_cap_reached(db, spec.usage_name)
            last_error = failures.get(spec.step) if (spec.step and enabled) else None
        else:
            used, cap, reached, last_error = 0, None, False, None

        rows.append(
            {
                "provider": spec.key,
                "label": spec.label,
                "role": spec.role,
                "implemented": spec.implemented,
                "configured": configured,
                "enabled": enabled,
                "calls_today": used,
                "cap": cap,
                "cap_reached": reached,
                "last_error": last_error,
                "note": spec.note,
            }
        )
    return rows


__all__ = ["ProviderSpec", "SPECS", "provider_status"]
