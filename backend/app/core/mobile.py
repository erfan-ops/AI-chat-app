"""Iranian mobile-number normalization.

The database column is ``USERS.MOBILE_NUMBER NUMBER(10)``, so the only storable
form is the local 10-digit number (``9123456789``) — the international form
(``989123456789``) does not fit, and the ``+98`` prefix is presentation only.
SMS.ir takes that same 10-digit local number.
"""

from __future__ import annotations

import re

from app.exceptions import BadRequestError

# ASCII digits on purpose. Python's ``\d`` also matches Persian and Arabic-Indic
# digits, so a number typed on a Persian keyboard would pass a ``\d``-based check
# and then either break the NUMBER(10) bind or silently become a different number.
LOCAL_MOBILE_PATTERN = re.compile(r"9[0-9]{9}")

# Separators users type or paste; stripped before validation.
_SEPARATORS = re.compile(r"[\s\-().‌‏‪-‮]")
_COUNTRY_CODE_PREFIXES = ("+98", "0098", "98")


def normalize_mobile(raw: str) -> str:
    """Return the canonical 10-digit local mobile, or raise ``BadRequestError``.

    Accepts ``912 345 6789``-style separators and rejects everything else with a
    message that says what to fix. The explicit prefix checks exist so a full
    international number is never quietly accepted as a local one.
    """
    value = _SEPARATORS.sub("", raw or "")
    if not value:
        raise BadRequestError("Enter your mobile number")
    if not value.isascii():
        raise BadRequestError("Use English digits (0-9) for your mobile number")
    if value.startswith("+"):
        raise BadRequestError("Enter the number without the country code (+98)")
    if value.startswith("00") or value.startswith("98"):
        raise BadRequestError("Enter the number without the country code (98)")
    if value.startswith("0"):
        raise BadRequestError("Enter the number without the leading zero")
    if LOCAL_MOBILE_PATTERN.fullmatch(value) is None:
        raise BadRequestError("Enter a 10-digit mobile number starting with 9")
    if len(set(value)) == 1:
        raise BadRequestError("Enter a valid mobile number")
    return value


def format_mobile(mobile: str) -> str:
    """Display form for a canonical local mobile: ``+98 912 345 6789``."""
    return f"+98 {mobile[:3]} {mobile[3:6]} {mobile[6:]}"
