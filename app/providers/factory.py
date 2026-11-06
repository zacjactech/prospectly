"""Provider factory — selects implementations based on configuration.

The service layer only imports from this factory; it never instantiates
provider SDKs directly. (SYSTEM_ARCHITECTURE §7)
"""

from __future__ import annotations

from app.config import settings
from app.providers.base import DiscoveryProvider, SearchProvider
from app.providers.ddgs import DuckDuckGoSearchProvider
from app.providers.osm import OverpassDiscoveryProvider
from app.providers.serper import SerperSearchProvider
from app.providers.tavily import TavilySearchProvider


def get_search_provider() -> SearchProvider | None:
    """Return the best available search provider, or None if none configured.

    Priority: Tavily (free tier) → Serper → DuckDuckGo (best-effort fallback).
    """
    if settings.tavily_api_key:
        return TavilySearchProvider(api_key=settings.tavily_api_key)
    if settings.serper_api_key:
        return SerperSearchProvider(api_key=settings.serper_api_key)
    # DuckDuckGo is always available as best-effort fallback.
    return DuckDuckGoSearchProvider()


def get_discovery_provider() -> DiscoveryProvider | None:
    """Return the discovery provider if OSM is enabled."""
    if settings.osm_enabled:
        return OverpassDiscoveryProvider()
    return None


__all__ = ["get_search_provider", "get_discovery_provider", "SearchProvider", "DiscoveryProvider"]
