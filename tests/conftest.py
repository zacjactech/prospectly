"""Shared pytest fixtures and helpers (provider fakes arrive in Phase 2 — AGENTS.md §7)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_external_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must never reach a real provider or read a developer key.

    AGENTS.md §7: no live external calls in tests. A developer's `.env` may
    configure AI_PROVIDER=gemini or a Tavily key — tests force the template
    floor, clear every key, disable keyless discovery, and reset the caps so
    behaviour is identical on every machine and in CI.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "ai_provider", "template")
    monkeypatch.setattr(settings, "ai_model", None)
    for attr in (
        "tavily_api_key",
        "serper_api_key",
        "brave_api_key",
        "gemini_api_key",
        "groq_api_key",
        "openrouter_api_key",
        "app_access_token",
    ):
        monkeypatch.setattr(settings, attr, None)
    monkeypatch.setattr(settings, "osm_enabled", False)
    monkeypatch.setattr(settings, "bizdata_enabled", False)
    monkeypatch.setattr(settings, "tavily_daily_cap", 1000)
    monkeypatch.setattr(settings, "serper_daily_cap", 500)
    monkeypatch.setattr(settings, "ai_daily_cap", 200)


def _client_with_csrf(client: TestClient) -> TestClient:
    """Prime a test client with a CSRF cookie by loading the dashboard.

    Subsequent POST calls must include ``csrf_token`` in form data. Use
    ``post_form()`` below to do this automatically.
    """
    client.get("/")
    return client


def post_form(client: TestClient, url: str, data: dict | None = None, **kwargs):
    """POST form data with the CSRF token automatically included."""
    if data is None:
        data = {}
    if "csrf_token" not in data:
        data["csrf_token"] = client.cookies.get("csrf_token", "")
    return client.post(url, data=data, **kwargs)


def post_json(client: TestClient, url: str, data: dict | None = None, **kwargs):
    """POST JSON with the CSRF token automatically included as a header."""
    if data is None:
        data = {}
    headers = kwargs.pop("headers", {})
    headers["X-CSRF-Token"] = client.cookies.get("csrf_token", "")
    return client.post(url, json=data, headers=headers, **kwargs)


def patch_json(client: TestClient, url: str, data: dict | None = None, **kwargs):
    """PATCH JSON with the CSRF token automatically included as a header."""
    if data is None:
        data = {}
    headers = kwargs.pop("headers", {})
    headers["X-CSRF-Token"] = client.cookies.get("csrf_token", "")
    return client.patch(url, json=data, headers=headers, **kwargs)
