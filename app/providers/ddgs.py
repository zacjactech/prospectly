"""DuckDuckGo HTML search provider — best-effort fallback, no key required.

Unofficial; subject to rate limiting and ToS restrictions. Never a hard
dependency — only used when no other search provider is configured.
"""

from __future__ import annotations

import httpx


class DuckDuckGoSearchProvider:
    """Best-effort DDG HTML scraping fallback."""

    def __init__(self, *, base_url: str = "https://html.duckduckgo.com/html/") -> None:
        self.base_url = base_url.rstrip("/")

    async def search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Prospectly/0.1)"}
        payload = {"q": query}
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            try:
                response = await client.post(self.base_url, data=payload)
                response.raise_for_status()
            except httpx.HTTPError:
                return []
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
