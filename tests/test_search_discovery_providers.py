"""Provider failure contracts + OSM query shape (live-smoke findings, 2026-11-10).

Live smoke showed two classes of defect:
1. Every provider swallowed HTTP errors and returned [] — indistinguishable
   from "no results", which could feed P6's `no_website_found` rule with a
   FAILED search/discovery. All providers now raise on total failure.
2. The OSM Overpass query only matched `shop=*` (hotels are `tourism=*`)
   and ignored locations entirely — it could never find a hotel in Berlin.

All tests use httpx.MockTransport — zero network (AGENTS §7).
"""

from __future__ import annotations

import httpx
import pytest

from app.providers.ddgs import DuckDuckGoSearchProvider
from app.providers.osm import OverpassDiscoveryProvider
from app.providers.serper import SerperSearchProvider
from app.providers.tavily import TavilySearchProvider

DDGS_HTML = """
<html><body>
<a class="result__a" href="https://example.com/one">First Result</a>
<a class="result__snippet">A snippet about the first result</a>
<a class="result__a" href="https://example.com/two">Second Result</a>
</body></html>
"""


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Search providers: failure must raise, success must parse
# ---------------------------------------------------------------------------


async def test_ddgs_parses_results() -> None:
    provider = DuckDuckGoSearchProvider(
        client=_client(lambda r: httpx.Response(200, text=DDGS_HTML))
    )
    results = await provider.search("anything", max_results=5)
    assert [r["url"] for r in results] == ["https://example.com/one", "https://example.com/two"]
    assert results[0]["title"] == "First Result"


async def test_ddgs_raises_on_http_error() -> None:
    provider = DuckDuckGoSearchProvider(client=_client(lambda r: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.search("anything", max_results=5)


async def test_tavily_raises_on_http_error() -> None:
    provider = TavilySearchProvider("key", client=_client(lambda r: httpx.Response(429)))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.search("anything", max_results=5)


async def test_tavily_parses_results() -> None:
    payload = {"results": [{"title": "T", "url": "https://t.example", "content": "C"}]}
    provider = TavilySearchProvider(
        "key", client=_client(lambda r: httpx.Response(200, json=payload))
    )
    results = await provider.search("anything", max_results=5)
    assert results == [{"title": "T", "url": "https://t.example", "snippet": "C"}]


async def test_serper_raises_on_http_error() -> None:
    provider = SerperSearchProvider("key", client=_client(lambda r: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.search("anything", max_results=5)


async def test_serper_parses_results() -> None:
    payload = {"organic": [{"title": "S", "link": "https://s.example", "snippet": "S2"}]}
    provider = SerperSearchProvider(
        "key", client=_client(lambda r: httpx.Response(200, json=payload))
    )
    results = await provider.search("anything", max_results=5)
    assert results == [{"title": "S", "url": "https://s.example", "snippet": "S2"}]


# ---------------------------------------------------------------------------
# OSM: query shape (the live-smoke defect) + failure contract
# ---------------------------------------------------------------------------


async def test_osm_query_scopes_area_and_covers_tourism_amenity_shop() -> None:
    """The query must scope to the location AND cover tourism= (hotels)."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qs

        seen["query"] = parse_qs(request.content.decode())["data"][0]
        return httpx.Response(
            200, json={"elements": [{"tags": {"name": "Hotel Adlon", "tourism": "hotel"}}]}
        )

    provider = OverpassDiscoveryProvider(client=_client(handler))
    results = await provider.discover(industries=["hotel"], locations=["Berlin"], max_results=10)

    q = seen["query"]
    assert 'area["name"="Berlin"]' in q, "must scope to the named location"
    assert "tourism" in q and "amenity" in q and "shop" in q, "must cover all three tag keys"
    assert "(hotel)" in q, "must search the industry as regex"
    assert "(area.a0)" in q, "clauses must reference the area"
    assert (
        results
        == [{"name": "Hotel Adlon", "website": "", "phone": "", "address": "", "category": ""}]
        or results[0]["name"] == "Hotel Adlon"
    )


async def test_osm_raises_on_http_error() -> None:
    provider = OverpassDiscoveryProvider(client=_client(lambda r: httpx.Response(504)))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.discover(industries=["hotel"], locations=["Berlin"], max_results=10)


async def test_osm_returns_empty_without_industries_or_locations() -> None:
    provider = OverpassDiscoveryProvider(client=_client(lambda r: httpx.Response(200, json={})))
    assert await provider.discover(industries=[], locations=["Berlin"], max_results=5) == []
    assert await provider.discover(industries=["hotel"], locations=[], max_results=5) == []
