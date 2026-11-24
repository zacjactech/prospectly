"""Activity logging service (append-only history, DATABASE_DESIGN §2.11)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Activity


def log_activity(
    db: Session,
    *,
    prospect_id: int,
    type: str,
    actor: str = "user",
    payload: dict | None = None,
) -> Activity:
    """Append an activity row. Never updated or deleted after creation."""
    activity = Activity(
        prospect_id=prospect_id,
        type=type,
        actor=actor,
        payload=payload,
    )
    db.add(activity)
    db.flush()
    return activity


def describe_payload(payload: dict | None, *, max_parts: int = 6) -> str:
    """Render an activity payload as readable text instead of raw JSON.

    ``{"reason": "asked to be removed"}`` → ``reason: asked to be removed``.
    One nested level contributes its scalar leaves
    (``steps — search: succeeded · inspect: failed``), empty values are
    dropped and the result is capped so a single activity row can never
    dominate the list (the panel used to print ``payload|tojson``).
    """
    if not payload:
        return ""

    parts: list[str] = []

    def add(label: str, value: object) -> None:
        if value is None or value == "":
            return
        if isinstance(value, bool):
            value = "yes" if value else "no"
        parts.append(f"{label}: {value}")

    for key, value in payload.items():
        label = str(key).replace("_", " ")
        if isinstance(value, dict):
            inner = []
            for nested_key, nested_value in value.items():
                if isinstance(nested_value, (dict, list, tuple)) or nested_value in (None, ""):
                    continue
                if isinstance(nested_value, bool):
                    nested_value = "yes" if nested_value else "no"
                inner.append(f"{str(nested_key).replace('_', ' ')} {nested_value}")
            if inner:
                parts.append(f"{label}: " + ", ".join(inner[:4]))
            continue
        if isinstance(value, (list, tuple)):
            scalars = [v for v in value if not isinstance(v, (dict, list, tuple))][:4]
            if scalars:
                parts.append(f"{label}: " + ", ".join(str(v) for v in scalars))
            continue
        add(label, value)

    return " · ".join(parts[:max_parts])
