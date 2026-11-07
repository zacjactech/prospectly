"""WhatsApp click-to-chat helpers (SYSTEM_ARCHITECTURE §8, D9).

Exactly two pure functions: normalize a phone number to E.164 digits, and build
the ``wa.me`` URL for a message. This module performs no network I/O and
imports no WhatsApp SDK, so no code path here can send anything (P2/P3/P4).
The route opens the returned URL in the OS browser and a human presses Send;
"sent" is recorded only by an explicit confirmation action.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import phonenumbers

_NON_DIGITS = re.compile(r"\D+")
# Formatting people type around a number: spaces, parens, dots, dashes, slashes.
_SEPARATORS = re.compile(r"[\s()./-]")


def normalize_phone(raw: str, default_country: str | None = None) -> str | None:
    """Return E.164 digits for ``raw``, or ``None`` when it cannot be trusted.

    Rules (TECHNOLOGY_DECISIONS D9):

    * formatting is stripped, so ``"+1 (415) 555-2671"`` and ``"14155552671"``
      normalize to the same value (they also dedupe as the same prospect);
    * a country code must be present — either in the number itself or, for a
      bare national number, supplied by ``default_country`` (ISO-3166 alpha-2).
      A national number with no configured country is rejected rather than
      guessed, because a wrong guess would send a message to a stranger;
    * the result must be a *valid* number per ``phonenumbers``. An invalid
      number is never normalized: the raw input stays in ``phone_raw`` alone
      (SECURITY_AND_PRIVACY §1).

    Returns digits without a leading ``+`` — the exact form ``wa.me`` expects.
    """
    text = (raw or "").strip()
    if not text:
        return None

    region = default_country.strip().upper() if default_country else None

    # Try the reading the caller most likely meant first: as typed (so a
    # national number with a configured country wins over a coincidental
    # international match), then the bare-international forms. ``phonenumbers``
    # refuses a country-code-without-``+`` string when it has no region to
    # guess from, so we re-present those as E.164 explicitly.
    for candidate in _candidates(text):
        try:
            parsed = phonenumbers.parse(candidate, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            return e164.lstrip("+")

    # Nothing parsed to a valid number: the input stays in ``phone_raw`` only.
    return None


def _candidates(text: str) -> list[str]:
    """Readable spellings of ``text`` to try, most-likely interpretation first."""
    compact = _SEPARATORS.sub("", text)
    candidates = [text]
    if compact.startswith("00") and compact[2:].isdigit():
        candidates.append("+" + compact[2:])  # 00 is the international prefix
    elif compact.isdigit():
        candidates.append("+" + compact)  # E.164 written without its '+'
    return candidates


def build_wa_me_url(e164: str, text: str) -> str:
    """Build the click-to-chat URL that opens WhatsApp with ``text`` prefilled.

    Format: ``https://wa.me/<E.164 digits>?text=<url-encoded>`` — the official
    click-to-chat shape (RESEARCH_FINDINGS §WhatsApp integration). Accepts the
    digits with or without a leading ``+``.

    Returns a URL and nothing more: opening it in the browser and actually
    sending are the caller's job, and this function never contacts WhatsApp.
    """
    digits = _NON_DIGITS.sub("", e164 or "")
    if not digits:
        raise ValueError("e164 must contain at least one digit")

    url = f"https://wa.me/{digits}"
    if text:
        url = f"{url}?text={quote(text, safe='')}"
    return url
