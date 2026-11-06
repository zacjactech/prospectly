"""SiteInspector — website status classification for prospect research.

Determines whether a prospect's website is reachable, unreachable, or could not
be verified, plus extracts signals (contact path, booking/CTA, tech hints).
(P6: distinguishes `no_website_found` from `check_failed`.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.safe_fetcher import FetchResult, SafeFetchError, fetch

# Headers that hint at common site generators / platforms.
_GENERATOR_HINTS = [
    ("wordpress", re.compile(r"wordpress", re.I)),
    ("shopify", re.compile(r"shopify", re.I)),
    ("wix", re.compile(r"wix.com|wixsite", re.I)),
    ("squarespace", re.compile(r"squarespace", re.I)),
    ("webflow", re.compile(r"webflow", re.I)),
    ("ghost", re.compile(r"ghost.org", re.I)),
    ("drupal", re.compile(r"drupal", re.I)),
    ("joomla", re.compile(r"joomla", re.I)),
]

_TECH_HINT_HEADERS = {
    "x-powered-by": "tech",
    "server": "tech",
    "x-generator": "tech",
}

_CONTACT_PATH_PATTERNS = [
    re.compile(r"/contact", re.I),
    re.compile(r"/contact-us", re.I),
    re.compile(r"/get-in-touch", re.I),
    re.compile(r"/book", re.I),
    re.compile(r"/schedule", re.I),
    re.compile(r"/appointment", re.I),
]


@dataclass
class SiteInspection:
    url: str
    outcome: str  # reachable | unreachable_http | dns_failure | timeout | blocked | invalid_url | redirect_loop | not_attempted
    http_status: int | None
    final_url: str | None
    response_ms: int | None
    title: str | None
    has_contact_path: bool
    has_booking_or_cta: bool
    signals: dict[str, Any]
    error: str | None

    @property
    def is_reachable(self) -> bool:
        return self.outcome == "reachable"


def _extract_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


def _detect_tech(result: FetchResult, html: str) -> list[str]:
    signals: list[str] = []
    headers = result.headers
    for header, _kind in _TECH_HINT_HEADERS.items():
        value = headers.get(header, "")
        if value:
            signals.append(f"{header}:{value}")
    for name, pattern in _GENERATOR_HINTS:
        if pattern.search(html) or pattern.search(str(headers)):
            signals.append(f"cms:{name}")
    return signals


def _has_contact_path(html: str, url: str) -> bool:
    lower = html.lower()
    if any(p.search(lower) for p in _CONTACT_PATH_PATTERNS):
        return True
    return bool(re.search(r"mailto:|tel:|wa\.me|whatsapp", html, re.I))


def _has_booking_or_cta(html: str) -> bool:
    lower = html.lower()
    cta_keywords = [
        "book now",
        "schedule",
        "appointment",
        "get started",
        "request quote",
        "contact us",
        "free consultation",
        "hire us",
        "work with us",
    ]
    return any(kw in lower for kw in cta_keywords)


async def inspect(url: str) -> SiteInspection:
    """Inspect a website URL and return a SiteInspection.

    Never raises SafeFetchError to callers — all errors are captured into the
    inspection outcome. Callers can trust this to always return a result.
    """
    try:
        result = await fetch(url)
    except SafeFetchError as exc:
        return SiteInspection(
            url=url,
            outcome=exc.code,
            http_status=None,
            final_url=None,
            response_ms=None,
            title=None,
            has_contact_path=False,
            has_booking_or_cta=False,
            signals={},
            error=exc.message,
        )

    html = ""
    content_type = result.headers.get("content-type", "")
    if "text/" in content_type or "html" in content_type:
        try:
            html = result.content.decode("utf-8", errors="replace")
        except Exception:
            html = ""

    title = _extract_title(html) if html else None
    tech_signals = _detect_tech(result, html)
    signals: dict[str, Any] = {
        "generator_meta": tech_signals,
        "word_count": len(html.split()) if html else 0,
        "language_hints": [],
        "content_type": content_type,
    }
    if result.content:
        signals["response_bytes"] = len(result.content)

    return SiteInspection(
        url=url,
        outcome=_classify_outcome(result.status_code, result.headers),
        http_status=result.status_code,
        final_url=result.final_url,
        response_ms=result.elapsed_ms,
        title=title,
        has_contact_path=_has_contact_path(html, url),
        has_booking_or_cta=_has_booking_or_cta(html),
        signals=signals,
        error=None,
    )


def _classify_outcome(status_code: int, headers: dict[str, str]) -> str:
    """Classify an HTTP response into a website_checks outcome."""
    if status_code >= 200 and status_code < 300:
        return "reachable"
    if status_code >= 300 and status_code < 400:
        return "reachable"  # redirects that httpx followed and landed
    if status_code >= 400 and status_code < 500:
        return "unreachable_http"
    if status_code >= 500:
        return "unreachable_http"
    return "unreachable_http"
