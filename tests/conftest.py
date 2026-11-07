"""Shared pytest fixtures and helpers (provider fakes arrive in Phase 2 — AGENTS.md §7)."""

from fastapi.testclient import TestClient


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
