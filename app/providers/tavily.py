"""Tavily search provider adapter."""

from __future__ import annotations

from typing import Any

import httpx


class TavilySearchProvider:
    """Tavily search API (https://tavily.com).

    Free tier: 1,000 credits/month. Requires TAVILY_API_KEY.
    """

    def __init__(self, api_key: str, *, base_url: str = "https://api.tavily.com") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        payload: dict[str, Any] = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_images": False,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(f"{self.base_url}/search", json=payload)
                response.raise_for_status()
            except httpx.HTTPError:
                return []
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
