"""Provider/AI usage accounting and daily cap enforcement (SYSTEM_ARCHITECTURE §6).

The caps are declared in ``config.py`` (``*_daily_cap``), documented in
``COST_ANALYSIS.md`` and shown on Settings → General. This module is the one
place that reads them and the one place that writes ``provider_usage`` /
``ai_usage``, so the numbers on the Usage page are the same numbers the
enforcement acts on.

Semantics (documented in COST_ANALYSIS §"Caps"):

- A cap applies **per provider per calendar day**, summed across kinds.
- ``used >= cap`` blocks the next call. ``cap = 0`` blocks every call.
- Only quota-bound providers have a cap. Unmetered/local sources (DDGS
  fallback, OSM/Overpass, BizData, SafeFetcher) are never blocked — there is
  nothing to spend.
- A blocked call is counted in ``provider_usage.rate_limited`` and the caller
  reports it as a *skipped* step — it is never silently ignored, and a skipped
  search is never evidence for ``no_website_found`` (P6).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AIUsage, ProviderUsage

# Only quota-bound providers are gated. Maps provider name → settings attribute.
_CAPPED_PROVIDERS: dict[str, str] = {
    "tavily": "tavily_daily_cap",
    "serper": "serper_daily_cap",
}


def daily_cap_for(provider: str) -> int | None:
    """Return today's cap for ``provider``, or None when it is uncapped."""
    attr = _CAPPED_PROVIDERS.get(provider)
    if attr is None:
        return None
    return int(getattr(settings, attr))


def provider_calls_today(db: Session, provider: str) -> int:
    """Total recorded calls for ``provider`` today (all kinds)."""
    rows = (
        db.query(ProviderUsage)
        .filter(ProviderUsage.provider == provider, ProviderUsage.period == date.today())
        .all()
    )
    return sum(row.calls for row in rows)


def provider_cap_reached(db: Session, provider: str) -> bool:
    """True when this provider has a cap and today's usage has hit it."""
    cap = daily_cap_for(provider)
    if cap is None:
        return False
    return provider_calls_today(db, provider) >= cap


def record_provider_usage(
    db: Session,
    *,
    provider: str,
    kind: str,
    calls: int = 1,
    errors: int = 0,
    rate_limited: int = 0,
) -> ProviderUsage:
    """Upsert provider accounting for (provider, kind, today)."""
    today = date.today()
    row = db.query(ProviderUsage).filter_by(provider=provider, kind=kind, period=today).first()
    if row is None:
        row = ProviderUsage(
            provider=provider,
            kind=kind,
            calls=calls,
            errors=errors,
            rate_limited=rate_limited,
            period=today,
        )
        db.add(row)
    else:
        row.calls += calls
        row.errors += errors
        row.rate_limited += rate_limited
    db.flush()
    return row


def record_ai_usage(
    db: Session,
    *,
    provider: str,
    model: str | None,
    task: str,
    calls: int = 1,
    ok_calls: int = 0,
    failed_calls: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> AIUsage:
    """Upsert AI accounting for (provider, model, task, today).

    This is the only writer of ``ai_usage``; Settings → Usage and the
    ``ai_daily_cap`` gate both read from these rows.
    """
    today = date.today()
    row = (
        db.query(AIUsage).filter_by(provider=provider, model=model, task=task, period=today).first()
    )
    if row is None:
        row = AIUsage(
            provider=provider,
            model=model,
            task=task,
            calls=calls,
            ok_calls=ok_calls,
            failed_calls=failed_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            period=today,
        )
        db.add(row)
    else:
        row.calls += calls
        row.ok_calls += ok_calls
        row.failed_calls += failed_calls
        row.prompt_tokens += prompt_tokens
        row.completion_tokens += completion_tokens
    db.flush()
    return row


def ai_calls_today(db: Session) -> int:
    """Total AI calls today across providers/models/tasks."""
    rows = db.query(AIUsage).filter(AIUsage.period == date.today()).all()
    return sum(row.calls for row in rows)


def ai_cap_state(db: Session) -> tuple[int, int]:
    """Return (cap, used_today)."""
    return int(settings.ai_daily_cap), ai_calls_today(db)


def ai_cap_reached(db: Session) -> bool:
    cap, used = ai_cap_state(db)
    return used >= cap


def usage_summary(db: Session) -> dict[str, dict[str, int | bool | None]]:
    """Cap + used-today per gated provider, for Settings → General."""
    summary: dict[str, dict[str, int | bool | None]] = {}
    for provider in _CAPPED_PROVIDERS:
        cap = daily_cap_for(provider)
        used = provider_calls_today(db, provider)
        summary[provider] = {
            "cap": cap,
            "used": used,
            "reached": cap is not None and used >= cap,
        }
    cap, used = ai_cap_state(db)
    summary["ai"] = {"cap": cap, "used": used, "reached": used >= cap}
    return summary


__all__ = [
    "ai_calls_today",
    "ai_cap_reached",
    "ai_cap_state",
    "daily_cap_for",
    "provider_calls_today",
    "provider_cap_reached",
    "record_ai_usage",
    "record_provider_usage",
    "usage_summary",
]
