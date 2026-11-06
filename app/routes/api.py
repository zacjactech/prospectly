"""JSON API router (`/api/v1`).

Endpoints land here starting Phase 1 (API_DESIGN.md); the router exists from
Phase 0 so app wiring and the OpenAPI export are exercised from day one.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["api"])
