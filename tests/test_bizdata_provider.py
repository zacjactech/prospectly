"""BizData discovery provider tests — no real network calls (D12).

Uses httpx.MockTransport to simulate the BizData API responses.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.providers.bizdata import BizDataDiscoveryProvider


def _make_provider(payload: list[dict]) -> BizDataDiscoveryProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("location")
        assert request.url.params.get("category")
        assert request.url.params.get("limit")
        return httpx.Response(200, text=json.dumps(payload))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return BizDataDiscoveryProvider(client=client)


@pytest.mark.asyncio
async def test_bizdata_discover_returns_businesses() -> None:
    payload = [
        {
            "name": "Acme Cafe",
            "website": "https://acme.example",
            "phone": "+1-555-0100",
            "address": "123 Main St, Berlin",
            "category": "cafe",
        },
        {
            "name": "Bakery GmbH",
            "website": "",
            "phone": "+1-555-0101",
            "address": "456 Marktpl., Berlin",
            "category": "bakery",
        },
    ]
    provider = _make_provider(payload)

    results = await provider.discover(
        industries=["cafe"],
        locations=["Berlin"],
        max_results=10,
    )

    assert len(results) == 2
    assert results[0]["name"] == "Acme Cafe"
    assert results[0]["website"] == "https://acme.example"
    assert results[0]["phone"] == "+1-555-0100"
    assert results[0]["address"] == "123 Main St, Berlin"
    assert results[1]["name"] == "Bakery GmbH"


@pytest.mark.asyncio
async def test_bizdata_discover_caps_at_max_results() -> None:
    payload = [
        {"name": f"Biz {i}", "address": f"{i} Main St", "category": "cafe"} for i in range(25)
    ]
    provider = _make_provider(payload)

    results = await provider.discover(
        industries=["cafe"],
        locations=["Berlin"],
        max_results=5,
    )

    assert len(results) == 5


@pytest.mark.asyncio
async def test_bizdata_discover_deduplicates_by_name_and_address() -> None:
    payload = [
        {"name": "Acme Cafe", "address": "123 Main St", "category": "cafe"},
        {"name": "Acme Cafe", "address": "123 Main St", "category": "cafe"},  # duplicate
        {"name": "Other Cafe", "address": "123 Main St", "category": "cafe"},
    ]
    provider = _make_provider(payload)

    results = await provider.discover(
        industries=["cafe"],
        locations=["Berlin"],
        max_results=10,
    )

    assert len(results) == 2
    names = {r["name"] for r in results}
    assert names == {"Acme Cafe", "Other Cafe"}


@pytest.mark.asyncio
async def test_bizdata_discover_raises_when_every_call_fails() -> None:
    """Total failure must RAISE (P6, live-smoke finding).

    Returning [] would be indistinguishable from "found nothing" and could
    feed the no_website_found rule with a failed search.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = BizDataDiscoveryProvider(client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await provider.discover(
            industries=["cafe"],
            locations=["Berlin"],
            max_results=10,
        )


@pytest.mark.asyncio
async def test_bizdata_discover_degrades_when_some_calls_fail() -> None:
    """Partial failure stays graceful: succeeding locations still return."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("location") == "Berlin":
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(
            200,
            json=[{"name": "Cafe Paris", "address": "1 Rue Ex", "website": "", "phone": ""}],
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = BizDataDiscoveryProvider(client=client)

    results = await provider.discover(
        industries=["cafe"],
        locations=["Berlin", "Paris"],
        max_results=10,
    )
    assert [r["name"] for r in results] == ["Cafe Paris"]


@pytest.mark.asyncio
async def test_bizdata_discover_empty_when_no_industries_or_locations() -> None:
    provider = BizDataDiscoveryProvider()
    results = await provider.discover(industries=[], locations=["Berlin"], max_results=10)
    assert results == []
    results = await provider.discover(industries=["cafe"], locations=[], max_results=10)
    assert results == []


@pytest.mark.asyncio
async def test_bizdata_discover_fans_out_over_industries_and_locations() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.url.params.get('category')}|{request.url.params.get('location')}")
        return httpx.Response(200, text=json.dumps([]))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = BizDataDiscoveryProvider(client=client)

    await provider.discover(
        industries=["cafe", "bakery"],
        locations=["Berlin", "Munich"],
        max_results=10,
    )

    assert len(calls) == 4
    assert "cafe|Berlin" in calls
    assert "bakery|Munich" in calls
