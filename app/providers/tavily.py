"""Tavily search provider adapter."""

from __future__ import annotations

from typing import Any

import httpx


class TavilySearchProvider:
    """Tavily search API (https://tavily.com).

    Free tier: 1,000 credits/month. Requires TAVILY_API_KEY.
    """

    provider_name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.tavily.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        payload: dict[str, Any] = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_images": False,
        }
        # HTTP failures RAISE (P6): a failed search must never read as
        # "no results" — the pipeline records it as a failed step.
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.post(f"{self.base_url}/search", json=payload)
            response.raise_for_status()
        finally:
            if own_client:
                await client.aclose()
        data = response.json()

        results: list[dict[str, str]] = []
        for item in data.get("results", [])[:max_results]:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                }
            )
        return results
