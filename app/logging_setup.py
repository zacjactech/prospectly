"""Logging setup: rotating file + console, with secret redaction (D10, SECURITY §4)."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from app.config import settings

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?i)\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*[:=]\s*[^\s,;]+"
        ),
        "***REDACTED***",
    ),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._\-]+"), r"\1***REDACTED***"),
]

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_configured = False


class RedactionFilter(logging.Filter):
    """Strip key-like values from every record before it is formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            message = str(record.msg)
        for pattern, replacement in _PATTERNS:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


def setup_logging() -> None:
    """Configure root logger once (idempotent)."""
    global _configured
    if _configured:
        return
    settings.ensure_dirs()

    formatter = logging.Formatter(_FORMAT)
    redaction = RedactionFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redaction)

    file_handler = RotatingFileHandler(
        settings.logs_dir / "app.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(console)
    root.addHandler(file_handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # noisy; app has its own logs
    _configured = True
