"""SafeFetcher redirect-chain tests (M6).

Zero network: DNS resolution is stubbed and all HTTP traffic goes through an
``httpx.MockTransport``. Each redirect hop must be re-validated before it is
requested (SSRF TOCTOU fix).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app import safe_fetcher
from app.safe_fetcher import SafeFetchError, fetch

Handler = Callable[[httpx.Request], httpx.Response]


def _stub_resolver(host: str) -> list[str]:
    """Resolve literal IPs as-is; map every hostname to a fixed public IP."""
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        return ["93.184.216.34"]


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_fetcher, "_resolve_host", _stub_resolver)


def _use_handler(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    """Route the client constructed inside ``fetch`` through a mock transport."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(safe_fetcher.httpx, "AsyncClient", factory)


async def test_redirect_to_private_ip_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    _use_handler(monkeypatch, handler)

    with pytest.raises(SafeFetchError) as exc_info:
        await fetch("http://public.example/start")

    assert exc_info.value.code == "blocked"
    assert requested == ["http://public.example/start"]


async def test_redirect_to_disallowed_scheme_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    _use_handler(monkeypatch, handler)

    with pytest.raises(SafeFetchError) as exc_info:
        await fetch("http://public.example/start")

    assert exc_info.value.code == "invalid_url"


async def test_public_redirect_chain_within_cap_is_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        path = request.url.path
        if path == "/start":
            return httpx.Response(302, headers={"location": "/one"})
        if path == "/one":
            return httpx.Response(301, headers={"location": "http://public.example/two"})
        return httpx.Response(200, text="ok", headers={"Content-Type": "text/plain"})

    _use_handler(monkeypatch, handler)

    result = await fetch("http://public.example/start", max_redirects=2)

    assert result.status_code == 200
    assert result.content == b"ok"
    assert result.final_url == "http://public.example/two"
    assert requested == [
        "http://public.example/start",
        "http://public.example/one",
        "http://public.example/two",
    ]


async def test_redirect_chain_exceeding_cap_raises_redirect_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        path = request.url.path
        if path == "/start":
            return httpx.Response(302, headers={"location": "/one"})
        if path == "/one":
            return httpx.Response(302, headers={"location": "/two"})
        return httpx.Response(200, text="unreachable")

    _use_handler(monkeypatch, handler)

    with pytest.raises(SafeFetchError) as exc_info:
        await fetch("http://public.example/start", max_redirects=1)

    assert exc_info.value.code == "redirect_loop"
    # Two redirect responses were followed; the third target was never requested.
    assert requested == ["http://public.example/start", "http://public.example/one"]


async def test_size_cap_still_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 64)

    _use_handler(monkeypatch, handler)

    with pytest.raises(SafeFetchError) as exc_info:
        await fetch("http://public.example/", max_bytes=8)

    assert exc_info.value.code == "size_exceeded"


async def test_original_ssrf_guards_still_apply() -> None:
    cases = {
        "file:///etc/passwd": "invalid_url",
        "http://127.0.0.1/": "blocked",
        "http://169.254.169.254/": "blocked",
        "http://10.0.0.1/": "blocked",
    }
    for url, expected_code in cases.items():
        with pytest.raises(SafeFetchError) as exc_info:
            await fetch(url)
        assert exc_info.value.code == expected_code, url
