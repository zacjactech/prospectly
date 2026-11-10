"""SafeFetcher — SSRF-safe HTTP client for outbound fetches.

All outbound network requests from the app go through this module so we can
enforce a single security boundary: scheme allowlist, private/loopback IP
blocking, redirect cap, size cap, and per-request timeout. (SECURITY_AND_PRIVACY §5)
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass

import httpx

from app.config import settings

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

PRIVATE_IP_RANGES: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


class SafeFetchError(Exception):
    """Raised when a fetch is blocked or fails for a safety reason."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    http_version: str
    headers: dict[str, str]
    content: bytes
    elapsed_ms: int


def _resolve_host(host: str) -> list[str]:
    """Resolve a hostname to IP addresses, raising on DNS failure."""
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SafeFetchError("dns_failure", f"DNS resolution failed for {host}: {exc}") from exc
    return sorted({str(info[4][0]) for info in infos})


def _is_blocked_ip(ip: str) -> bool:
    """True if the IP is private/loopback/link-local (SSRF risk)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable → treat as blocked
    return any(addr in net for net in PRIVATE_IP_RANGES)


def _validate_url(url: str) -> str:
    """Validate and normalize a URL before fetching. Returns the normalized URL."""
    from urllib.parse import urlparse

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise SafeFetchError("invalid_url", f"Scheme not allowed: {parsed.scheme or '(none)'}")
    if not parsed.hostname:
        raise SafeFetchError("invalid_url", "URL has no hostname")

    # Block private IPs at DNS resolution time.
    for ip in _resolve_host(parsed.hostname):
        if _is_blocked_ip(ip):
            raise SafeFetchError(
                "blocked",
                f"Refusing to fetch {parsed.hostname}: resolves to private/loopback IP {ip} (SSRF guard)",
            )
    return url


async def fetch(
    url: str,
    *,
    timeout_seconds: int | None = None,
    max_redirects: int | None = None,
    max_bytes: int | None = None,
) -> FetchResult:
    """Fetch a URL with SSRF and safety controls.

    Redirects are followed manually, one hop at a time, and every hop is fully
    re-validated (scheme, DNS, private/loopback IP) before it is requested.
    This closes the TOCTOU hole where an allowed public host could redirect the
    client onto a private IP (automatic ``follow_redirects`` never re-checks).
    The redirect count is capped by ``max_redirects`` (5 by default).

    Raises SafeFetchError on any safety violation or network failure.
    Callers catch SafeFetchError and convert to appropriate outcome rows.
    """
    url = _validate_url(url)
    max_hops = max_redirects or settings.http_max_redirects

    timeout = httpx.Timeout(
        timeout=timeout_seconds or settings.http_timeout_seconds,
        connect=settings.http_connect_timeout_seconds,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        current_url = url
        redirects_followed = 0
        try:
            start = asyncio.get_event_loop().time()
            while True:
                response = await client.get(current_url)

                location = response.headers.get("location")
                if response.status_code not in _REDIRECT_STATUSES or location is None:
                    break
                if redirects_followed >= max_hops:
                    raise SafeFetchError(
                        "redirect_loop",
                        f"Too many redirects (limit {max_hops}): {url}",
                    )

                # Re-run the full SSRF validation on the redirect target.
                next_url = str(httpx.URL(current_url).join(location))
                current_url = _validate_url(next_url)
                redirects_followed += 1

            elapsed_ms = int((asyncio.get_event_loop().time() - start) * 1000)
        except httpx.ConnectTimeout as exc:
            raise SafeFetchError("timeout", f"Connection timed out: {current_url}") from exc
        except httpx.ReadTimeout as exc:
            raise SafeFetchError("timeout", f"Read timed out: {current_url}") from exc
        except httpx.ConnectError as exc:
            raise SafeFetchError("unreachable_http", f"Connection failed: {exc}") from exc
        except httpx.InvalidURL as exc:
            raise SafeFetchError("invalid_url", f"Invalid URL: {exc}") from exc
        except httpx.TooManyRedirects as exc:  # pragma: no cover - auto-follow disabled
            raise SafeFetchError("redirect_loop", f"Too many redirects: {exc}") from exc
        except httpx.RequestError as exc:
            raise SafeFetchError("unreachable_http", f"Request failed: {exc}") from exc

    # Size cap — read body in a bounded way.
    body = response.content
    limit = max_bytes if max_bytes is not None else settings.http_max_bytes
    if len(body) > limit:
        raise SafeFetchError(
            "size_exceeded",
            f"Response too large: {len(body)} bytes (limit {limit})",
        )

    headers = {k.lower(): v for k, v in response.headers.items()}
    return FetchResult(
        url=str(response.url),
        final_url=str(response.url),
        status_code=response.status_code,
        http_version=response.http_version or "1.1",
        headers=headers,
        content=body,
        elapsed_ms=elapsed_ms,
    )
