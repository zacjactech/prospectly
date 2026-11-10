"""DuckDuckGo HTML search provider — best-effort fallback, no key required.

Unofficial; subject to rate limiting and ToS restrictions. Never a hard
dependency — only used when no other search provider is configured.

HTTP failures RAISE (live-smoke finding): returning [] on an error would be
indistinguishable from "no results" and could feed P6's no_website_found
rule with a failed search. The pipeline records the failure as a failed step.
"""

from __future__ import annotations

import httpx


class DuckDuckGoSearchProvider:
    """Best-effort DDG HTML scraping fallback."""

    provider_name = "ddgs"

    def __init__(
        self,
        *,
        base_url: str = "https://html.duckduckgo.com/html/",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Prospectly/0.1)"}
        payload = {"q": query}
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0, headers=headers)
        try:
            response = await client.post(self.base_url, data=payload)
            response.raise_for_status()
        finally:
            if own_client:
                await client.aclose()
        html = response.text

        results: list[dict[str, str]] = []
        # Very light-weight parsing: look for result__a links and result__snippet.
        import re

        link_matches = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        )
        snippet_matches = re.findall(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        )

        for i, (href, title) in enumerate(link_matches[:max_results]):
            clean_title = re.sub(r"<[^>]+>", "", title).strip()
            snippet = ""
            if i < len(snippet_matches):
                snippet = re.sub(r"<[^>]+>", "", snippet_matches[i]).strip()
            results.append({"title": clean_title, "url": href, "snippet": snippet})
        return results
