"""BizData discovery provider — free, no API key, no billing account.

Built on OpenStreetMap data via the BizData public API (https://bizdata-web.vercel.app).
Completely free and unlimited; no signup required. Falls back gracefully when
the public instance is unreachable.

See docs/RESEARCH_FINDINGS.md §4.1 and COST_ANALYSIS.md for the free-alternative
research that led to this choice.
"""

from __future__ import annotations

import httpx


class BizDataDiscoveryProvider:
    """BizData business discovery API wrapper.

    Endpoint: GET https://bizdata-web.vercel.app/api/businesses
      Params: location, category, limit
    Returns: JSON list of {name, website, phone, address, category, …}

    An optional ``httpx.AsyncClient`` may be injected (useful for tests with
    :class:`httpx.MockTransport`). When omitted, a short-lived client is created
    per ``discover`` call.
    """

    provider_name = "bizdata"

    def __init__(
        self,
        *,
        base_url: str = "https://bizdata-web.vercel.app",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def discover(
        self,
        *,
        industries: list[str],
        locations: list[str],
        max_results: int,
    ) -> list[dict[str, str]]:
        """Search for candidate businesses across industries × locations.

        BizData accepts a single location and category per call, so we fan out
        over the cartesian product and de-duplicate by name+address.
        """
        if not industries or not locations:
            return []

        results: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        per_call = max(1, max_results // max(1, len(industries) * len(locations)))

        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        failures = 0
        last_error: httpx.HTTPError | None = None
        try:
            for industry in industries:
                for location in locations:
                    if len(results) >= max_results:
                        break
                    try:
                        response = await client.get(
                            f"{self.base_url}/api/businesses",
                            params={
                                "location": location,
                                "category": industry,
                                "limit": per_call,
                            },
                        )
                        response.raise_for_status()
                    except httpx.HTTPError as exc:
                        # Partial degradation stays graceful, but a run where
                        # EVERY call failed is an error, not an empty result
                        # (live-smoke finding — same P6 reasoning as search).
                        failures += 1
                        last_error = exc
                        continue

                    data = response.json()
                    if not isinstance(data, list):
                        continue

                    for item in data:
                        if len(results) >= max_results:
                            break
                        name = (item.get("name") or "").strip()
                        address = (item.get("address") or "").strip()
                        key = (name.lower(), address.lower())
                        if not name or key in seen:
                            continue
                        seen.add(key)
                        results.append(
                            {
                                "name": name,
                                "website": item.get("website", "") or "",
                                "phone": item.get("phone", "") or "",
                                "address": address,
                                "category": item.get("category", industry) or industry,
                            }
                        )
                if len(results) >= max_results:
                    break
            if not results and failures and last_error is not None:
                raise last_error
        finally:
            if own_client:
                await client.aclose()

        return results


__all__ = ["BizDataDiscoveryProvider"]
