"""Phase 3 tests: phone normalization and wa.me URL building (D9, §8).

Table-driven over the input shapes named in the roadmap exit criteria:
E.164 with and without ``+``, national numbers with a configured default
country, and everything that must be rejected rather than guessed.
"""

from __future__ import annotations

import pytest

from app.whatsapp import build_wa_me_url, normalize_phone

# International format: country code present, written every way people write it.
INTERNATIONAL = [
    ("+442079460958", None, "442079460958"),
    ("442079460958", None, "442079460958"),  # E.164 without the '+'
    ("+44 20 7946 0958", None, "442079460958"),  # formatted
    ("44 20 7946 0958", None, "442079460958"),  # formatted, no '+'
    ("00 44 20 7946 0958", None, "442079460958"),  # 00 international prefix
    ("+1 (415) 555-2671", None, "14155552671"),
    ("14155552671", None, "14155552671"),
]

# National numbers: accepted only when a default country supplies the code.
NATIONAL_WITH_COUNTRY = [
    ("02079460958", "GB", "442079460958"),
    ("020 7946 0958", "gb", "442079460958"),  # region is case-insensitive
    ("2024561111", "US", "12024561111"),
    ("030901820", "DE", "4930901820"),
    ("0142685300", "FR", "33142685300"),
]

# A default country must not hijack a number that already carries its own code.
COUNTRY_DOES_NOT_OVERRIDE = [
    ("+442079460958", "US", "442079460958"),
    ("442079460958", "US", "442079460958"),  # pasted intl while US is default
]

# Rejected: a wrong guess would send a message to a stranger, so we send none.
REJECTED = [
    ("", None, None),
    ("   ", None, None),
    ("not-a-number", None, None),
    ("123", None, None),
    ("+12", None, None),
    ("+999123456", None, None),
    # National with no default country: country code absent, not guessed.
    ("02079460958", None, None),
    ("2024561111", None, None),
    ("08012345678", None, None),
    ("", "GB", None),
    ("call me", "GB", None),
]


@pytest.mark.parametrize(
    ("raw", "country", "expected"),
    INTERNATIONAL + NATIONAL_WITH_COUNTRY + COUNTRY_DOES_NOT_OVERRIDE + REJECTED,
    ids=[
        f"{r!r}/{c}"
        for r, c, _ in INTERNATIONAL + NATIONAL_WITH_COUNTRY + COUNTRY_DOES_NOT_OVERRIDE + REJECTED
    ],
)
def test_normalize_phone(raw: str, country: str | None, expected: str | None) -> None:
    result = normalize_phone(raw, country)
    assert result == expected
    if result is not None:
        # Digits only, and long enough to be an actual international number.
        assert result.isdigit()
        assert len(result) >= 8


def test_normalize_is_idempotent() -> None:
    """Normalizing an already-normalized number changes nothing (stable dedupe key)."""
    once = normalize_phone("+44 20 7946 0958")
    assert once is not None
    assert normalize_phone(once) == once


@pytest.mark.parametrize(
    ("e164", "text", "expected"),
    [
        ("442079460958", "Hello", "https://wa.me/442079460958?text=Hello"),
        ("+442079460958", "Hello", "https://wa.me/442079460958?text=Hello"),
        ("442079460958", "", "https://wa.me/442079460958"),
        # The message is a query value: spaces, ampersands and newlines encoded.
        ("442079460958", "Hi & bye", "https://wa.me/442079460958?text=Hi%20%26%20bye"),
        ("442079460958", "line1\nline2", "https://wa.me/442079460958?text=line1%0Aline2"),
        ("442079460958", "café", "https://wa.me/442079460958?text=caf%C3%A9"),
        # URL-unsafe characters never break out of the query.
        ("442079460958", "a?b#c&d=e", "https://wa.me/442079460958?text=a%3Fb%23c%26d%3De"),
    ],
)
def test_build_wa_me_url(e164: str, text: str, expected: str) -> None:
    assert build_wa_me_url(e164, text) == expected


@pytest.mark.parametrize("bad", ["", "   ", "+", "abc"])
def test_build_wa_me_url_rejects_numberless_input(bad: str) -> None:
    """A URL without a recipient would open a chat with nobody — refuse it."""
    with pytest.raises(ValueError):
        build_wa_me_url(bad, "Hello")


def test_module_performs_no_network_io() -> None:
    """D9: whatsapp.py must not import an HTTP client or WhatsApp SDK."""
    import app.whatsapp as mod

    with open(mod.__file__, encoding="utf-8") as fh:
        source = fh.read()
    for forbidden in ("httpx", "requests", "urllib.request", "webbrowser", "socket"):
        assert forbidden not in source
