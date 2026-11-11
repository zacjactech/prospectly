"""Settings → Providers status page + JSON twin (API_DESIGN §1, §2.8).

This was the Phase 2 exit criterion *"Settings → Providers status page"* —
documented twice in API_DESIGN but never built (it 404'd). These tests pin
the contract:

- both surfaces render and return the documented fields;
- **key values never appear** in either response (D8 / SECURITY §4);
- a config variable with no adapter (Brave) is reported honestly as not
  wired rather than as a working provider;
- caps and the most recent failure are surfaced from real persisted data.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import AIUsage, Prospect, ProviderUsage, ResearchRun, Source


def _client_with_schema() -> TestClient:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    client._test_db = db  # type: ignore[attr-defined]
    client._test_db_path = path  # type: ignore[attr-defined]
    return client


def _cleanup(client: TestClient) -> None:
    db = getattr(client, "_test_db", None)
    path = getattr(client, "_test_db_path", None)
    app.dependency_overrides.clear()
    if db is not None:
        db.close()
    if path is not None and os.path.exists(path):
        os.unlink(path)


def _row(payload: list[dict], provider: str) -> dict:
    matches = [r for r in payload if r["provider"] == provider]
    assert matches, f"{provider} missing from payload"
    return matches[0]


# ---------------------------------------------------------------------------
# Both surfaces exist and carry the documented contract
# ---------------------------------------------------------------------------


def test_html_page_renders_every_provider() -> None:
    client = _client_with_schema()
    try:
        response = client.get("/settings/providers")
        assert response.status_code == 200
        body = response.text
        for label in (
            "Tavily",
            "Serper",
            "Brave Search",
            "DuckDuckGo (HTML)",
            "BizData",
            "OpenStreetMap Overpass",
            "Website fetch (SafeFetcher)",
            "Gemini",
            "Groq",
            "OpenRouter",
            "Ollama (local)",
            "Template floor",
        ):
            assert label in body, f"{label} missing from the Providers page"
        assert "not wired" in body  # Brave is reported honestly
        assert "Keys are never displayed" in body
    finally:
        _cleanup(client)


def test_json_endpoint_returns_documented_fields() -> None:
    client = _client_with_schema()
    try:
        response = client.get("/api/v1/settings/providers")
        assert response.status_code == 200
        payload = response.json()
        assert isinstance(payload, list) and len(payload) >= 10
        for item in payload:
            for field in (
                "provider",
                "label",
                "role",
                "implemented",
                "configured",
                "enabled",
                "calls_today",
                "cap",
                "cap_reached",
                "last_error",
            ):
                assert field in item, f"{field} missing from {item.get('provider')}"
            assert isinstance(item["configured"], bool)
            assert isinstance(item["enabled"], bool)
    finally:
        _cleanup(client)


# ---------------------------------------------------------------------------
# Key hygiene: presence booleans only, never values
# ---------------------------------------------------------------------------


def test_key_values_never_appear_in_either_surface(
    monkeypatch,
) -> None:
    sentinel = "tvly-SENTINEL-DO-NOT-LEAK-0123456789"
    monkeypatch.setattr(settings, "tavily_api_key", sentinel)
    monkeypatch.setattr(settings, "gemini_api_key", "AIza-SENTINEL-DO-NOT-LEAK")

    client = _client_with_schema()
    try:
        html = client.get("/settings/providers").text
        js = client.get("/api/v1/settings/providers").text
        for surface in (html, js):
            assert sentinel not in surface
            assert "SENTINEL" not in surface

        # …but presence is reported.
        payload = client.get("/api/v1/settings/providers").json()
        assert _row(payload, "tavily")["configured"] is True
        assert _row(payload, "tavily")["enabled"] is True
    finally:
        _cleanup(client)


def test_missing_key_means_not_configured_and_not_enabled() -> None:
    # conftest's autouse fixture clears every key before each test.
    client = _client_with_schema()
    try:
        payload = client.get("/api/v1/settings/providers").json()
        tavily = _row(payload, "tavily")
        assert tavily["configured"] is False
        assert tavily["enabled"] is False
    finally:
        _cleanup(client)


# ---------------------------------------------------------------------------
# Honesty about what is actually wired
# ---------------------------------------------------------------------------


def test_brave_is_reported_as_config_only() -> None:
    client = _client_with_schema()
    try:
        payload = client.get("/api/v1/settings/providers").json()
        brave = _row(payload, "brave")
        assert brave["implemented"] is False
        assert brave["enabled"] is False, "an unwired provider must never read as enabled"
        assert "no adapter" in (brave["note"] or "")
    finally:
        _cleanup(client)


def test_keyless_flags_control_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "osm_enabled", False)
    monkeypatch.setattr(settings, "bizdata_enabled", True)
    client = _client_with_schema()
    try:
        payload = client.get("/api/v1/settings/providers").json()
        assert _row(payload, "osm")["enabled"] is False
        assert _row(payload, "bizdata")["enabled"] is True
        # The floor is always available.
        assert _row(payload, "template")["enabled"] is True
    finally:
        _cleanup(client)


def test_ddgs_is_labelled_as_conditional_fallback() -> None:
    client = _client_with_schema()
    try:
        payload = client.get("/api/v1/settings/providers").json()
        ddgs = _row(payload, "ddgs")
        assert ddgs["enabled"] is True
        assert "fallback" in (ddgs["note"] or "")
    finally:
        _cleanup(client)


# ---------------------------------------------------------------------------
# Usage, caps and last failure come from persisted data
# ---------------------------------------------------------------------------


def test_cap_state_reflects_persisted_usage(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tavily_daily_cap", 5)
    client = _client_with_schema()
    try:
        db = client._test_db  # type: ignore[attr-defined]
        db.add(ProviderUsage(provider="tavily", kind="search", calls=5, period=date.today()))
        db.flush()

        payload = client.get("/api/v1/settings/providers").json()
        tavily = _row(payload, "tavily")
        assert tavily["calls_today"] == 5
        assert tavily["cap"] == 5
        assert tavily["cap_reached"] is True
    finally:
        _cleanup(client)


def test_last_error_comes_from_the_most_recent_failed_step(monkeypatch) -> None:
    # Failures only surface for providers that are actually active.
    monkeypatch.setattr(settings, "tavily_api_key", "tvly-test")
    monkeypatch.setattr(settings, "serper_api_key", "serper-test")
    monkeypatch.setattr(settings, "bizdata_enabled", True)
    client = _client_with_schema()
    try:
        db = client._test_db  # type: ignore[attr-defined]
        source = db.query(Source).filter_by(type="manual").first()
        if source is None:
            source = Source(type="manual", label="Manual add", detail={})
            db.add(source)
            db.flush()
        prospect = Prospect(
            business_name="Acme",
            normalized_name="acme",
            stage="new",
            source_id=source.id,
        )
        db.add(prospect)
        db.flush()

        # An older failure that must be superseded…
        db.add(
            ResearchRun(
                prospect_id=prospect.id,
                status="partially_completed",
                steps={"search": "failed: older error"},
                finished_at=datetime.now(UTC),
            )
        )
        # …and a newer one that is the "last" failure.
        db.add(
            ResearchRun(
                prospect_id=prospect.id,
                status="partially_completed",
                steps={"search": "failed: 429 too many requests"},
                finished_at=datetime.now(UTC),
            )
        )
        db.add(
            ResearchRun(
                prospect_id=prospect.id,
                status="partially_completed",
                steps={"discovery": "failed: connection refused"},
                finished_at=datetime.now(UTC),
            )
        )
        db.flush()

        payload = client.get("/api/v1/settings/providers").json()
        assert _row(payload, "tavily")["last_error"] == "429 too many requests"
        assert _row(payload, "bizdata")["last_error"] == "connection refused"
        # A provider with no recorded failure shows none.
        assert _row(payload, "serper")["last_error"] is None
    finally:
        _cleanup(client)


def test_ai_last_error_comes_from_failed_usage(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "ai_provider", "gemini")
    client = _client_with_schema()
    try:
        db = client._test_db  # type: ignore[attr-defined]
        db.add(
            AIUsage(
                provider="gemini",
                model="gemini-3.5-flash",
                task="draft",
                calls=3,
                ok_calls=2,
                failed_calls=1,
                period=date.today(),
            )
        )
        db.flush()

        payload = client.get("/api/v1/settings/providers").json()
        gemini = _row(payload, "gemini")
        assert gemini["last_error"] == "1 failed call(s) today"
        assert gemini["calls_today"] == 3
    finally:
        _cleanup(client)


def test_page_and_json_agree(monkeypatch) -> None:
    """The HTML page and the JSON twin are built from one service call."""
    monkeypatch.setattr(settings, "tavily_daily_cap", 0)
    client = _client_with_schema()
    try:
        db = client._test_db  # type: ignore[attr-defined]
        db.add(ProviderUsage(provider="tavily", kind="search", calls=1, period=date.today()))
        db.flush()

        payload = client.get("/api/v1/settings/providers").json()
        html = client.get("/settings/providers").text

        assert _row(payload, "tavily")["cap_reached"] is True
        assert "1 / 0" in html and "blocked" in html
    finally:
        _cleanup(client)
