"""Serper search provider adapter."""

from __future__ import annotations

from typing import Any

import httpx


class SerperSearchProvider:
    """Serper.dev Google search API (https://serper.dev).

    Requires SERPER_API_KEY.
    """

    provider_name = "serper"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://google.serper.dev",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"q": query, "num": max_results}
        # HTTP failures RAISE (P6): a failed search must never read as
        # "no results" — the pipeline records it as a failed step.
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.post(f"{self.base_url}/search", json=payload, headers=headers)
            response.raise_for_status()
        finally:
            if own_client:
                await client.aclose()
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
