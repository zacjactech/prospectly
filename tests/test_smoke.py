"""Smoke tests: app boots, health probe answers, home page renders (Phase 0 exit criteria).

Uses a temp-file SQLite schema (same pattern as test_phase1.py) because the
home page renders dashboard metrics — on a fresh checkout the default
database has no tables, which made this test environment-dependent.
"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app


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
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.clear()


def test_healthz_returns_ok() -> None:
    client = _client_with_schema()
    try:
        response = client.get("/healthz")
    finally:
        _cleanup()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_renders_placeholder() -> None:
    client = _client_with_schema()
    try:
        response = client.get("/")
    finally:
        _cleanup()
    assert response.status_code == 200
    assert "Prospectly" in response.text


def test_security_headers_present() -> None:
    client = _client_with_schema()
    try:
        response = client.get("/healthz")
    finally:
        _cleanup()
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    # The only style-src exception is the hash of the .htmx-indicator CSS that
    # the vendored htmx.min.js injects (browser-verified hash; see app/main.py).
    assert "'sha256-faU7yAF8NxuMTNEwVmBz+VcYeIoBQ2EMHW3WaVxCvnk='" in csp
    assert "unsafe-inline" not in csp


def test_openapi_document_available() -> None:
    client = _client_with_schema()
    try:
        response = client.get("/api/openapi.json")
    finally:
        _cleanup()
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Prospectly"
