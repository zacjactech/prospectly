"""Provider protocol interfaces and shared types."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SearchProvider(Protocol):
    """Search the web for businesses matching a query."""

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        """Return list of {title, url, snippet} dicts."""
        ...


@runtime_checkable
class DiscoveryProvider(Protocol):
    """Discover candidate businesses from a target profile."""

    async def discover(
        self,
        *,
        industries: list[str],
        locations: list[str],
        max_results: int,
    ) -> list[dict[str, str]]:
        """Return list of candidate businesses with name, website, phone, address."""
        ...
