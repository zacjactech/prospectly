"""OpenStreetMap Overpass discovery provider.

Finds POIs/businesses by tag + area using the free Overpass API. No key
required. Results are a starting point, not an authoritative directory.
"""

from __future__ import annotations

import re

import httpx


class OverpassDiscoveryProvider:
    """OSM Overpass API discovery."""

    provider_name = "osm_overpass"

    def __init__(
        self,
        *,
        base_url: str = "https://overpass-api.de/api/interpreter",
        timeout_seconds: int = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def discover(
        self,
        *,
        industries: list[str],
        locations: list[str],
        max_results: int,
    ) -> list[dict[str, str]]:
        if not industries or not locations:
            return []

        # Escape user profile strings for the OQL string + regex contexts.
        tag_filters = "|".join(re.escape(ind.strip()) for ind in industries if ind.strip())
        area_names = [
            loc.strip().replace("\\", "").replace('"', "")[:80] for loc in locations if loc.strip()
        ][:3]
        if not tag_filters or not area_names:
            return []

        # Live-smoke finding: the previous query only matched shop=* (hotels
        # are tourism=*) and ignored locations entirely — it could never find
        # e.g. a hotel in Berlin. Overpass resolves named admin areas itself,
        # so scope by area (no Nominatim needed) and cover shop+amenity+tourism
        # on nodes and ways.
        area_decls = "\n".join(
            f'area["name"="{name}"]->.a{i};' for i, name in enumerate(area_names)
        )
        clauses: list[str] = []
        for i in range(len(area_names)):
            for key in ("shop", "amenity", "tourism"):
                for element in ("node", "way"):
                    clauses.append(f'  {element}["{key}"~"({tag_filters})"](area.a{i});')
        query = (
            f"[out:json][timeout:{self.timeout_seconds}];\n"
            f"{area_decls}\n"
            f"(\n" + "\n".join(clauses) + "\n);\n"
            f"out tags center {max_results};"
        )

        # HTTP failures RAISE (P6): a failed discovery must read as a failed
        # step (partially_completed), not as "found nothing".
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=float(self.timeout_seconds))
        try:
            response = await client.post(self.base_url, data={"data": query})
            response.raise_for_status()
        finally:
            if own_client:
                await client.aclose()
        data = response.json()

        results: list[dict[str, str]] = []
        for element in data.get("elements", [])[:max_results]:
            tags = element.get("tags", {})
            name = tags.get("name", "")
            if not name:
                continue
            results.append(
                {
                    "name": name,
                    "website": tags.get("website", ""),
                    "phone": tags.get("phone", tags.get("contact:phone", "")),
                    "address": tags.get("addr:full", "")
                    or f"{tags.get('addr:housenumber', '')} {tags.get('addr:street', '')}".strip(),
                    "category": tags.get("shop", tags.get("amenity", tags.get("tourism", ""))),
                }
            )
        return results
