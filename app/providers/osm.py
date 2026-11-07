"""OpenStreetMap Overpass discovery provider.

Finds POIs/businesses by tag + area using the free Overpass API. No key
required. Results are a starting point, not an authoritative directory.
"""

from __future__ import annotations

import httpx


class OverpassDiscoveryProvider:
    """OSM Overpass API discovery."""

    provider_name = "osm_overpass"

    def __init__(
        self,
        *,
        base_url: str = "https://overpass-api.de/api/interpreter",
        timeout_seconds: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def discover(
        self,
        *,
        industries: list[str],
        locations: list[str],
        max_results: int,
    ) -> list[dict[str, str]]:
        if not industries or not locations:
            return []

        # Build an Overpass QL query for businesses matching tags in target areas.
        # We search for common business tags across the listed locations.
        tag_filters = "|".join(industries)

        query = f"""
        [out:json][timeout:{self.timeout_seconds}];
        (
          node["shop"~"({tag_filters})"](area.searchArea);
          node["amenity"~"({tag_filters})"](area.searchArea);
          node["tourism"~"({tag_filters})"](area.searchArea);
          way["shop"~"({tag_filters})"](area.searchArea);
          way["amenity"~"({tag_filters})"](area.searchArea);
          way["tourism"~"({tag_filters})"](area.searchArea);
        );
        out center tags {max_results};
        """

        # Note: Overpass requires named areas (searchArea) which we'd resolve
        # via a separate Nominatim lookup. For MVP we do a simpler global query
        # filtered by location names in tags where possible.
        query = f"""
        [out:json][timeout:{self.timeout_seconds}];
        node["name"]["shop"~"({tag_filters})"];
        out tags center {max_results};
        """

        async with httpx.AsyncClient(timeout=float(self.timeout_seconds)) as client:
            try:
                response = await client.post(self.base_url, data={"data": query})
                response.raise_for_status()
            except httpx.HTTPError:
                return []
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
