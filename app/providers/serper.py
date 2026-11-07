"""Serper search provider adapter."""

from __future__ import annotations

from typing import Any

import httpx


class SerperSearchProvider:
    """Serper.dev Google search API (https://serper.dev).

    Requires SERPER_API_KEY.
    """

    provider_name = "serper"

    def __init__(self, api_key: str, *, base_url: str = "https://google.serper.dev") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"q": query, "num": max_results}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/search", json=payload, headers=headers
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return []
            data = response.json()

        results: list[dict[str, str]] = []
        for item in data.get("organic", [])[:max_results]:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                }
            )
        return results
