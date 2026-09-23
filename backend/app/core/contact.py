"""How a verification code reaches a user: by SMS or by email.

Two words carry the whole feature: a *method* (``SMS`` | ``EMAIL``) and the
*destination* it needs (a local mobile number, or an email address). The value set
is a Python ``Literal`` rather than a database enum, because the Oracle schema has
no check constraints — ``STATUS``, ``ROLE`` and ``MEMORY_TYPE`` are all conventions
the application enforces, and this follows them.
"""

from __future__ import annotations

from typing import Literal

from app.core.email import mask_email
from app.core.mobile import mask_mobile

OtpMethod = Literal["SMS", "EMAIL"]

OTP_METHODS: tuple[OtpMethod, ...] = ("SMS", "EMAIL")

# Why a code was issued. It decides the wording of the email (sign in vs confirm an
# address), keeps a code minted for one flow from being spent on the other, and is
# written to OTP_LOG.PURPOSE — so the names are what a reader of that history sees.
Purpose = Literal["login", "verify_contact"]

# ``USERS.PREFERRED_OTP_METHOD`` is nullable and holds no default, so NULL means
# SMS: every account that enabled two-step verification before email existed is an
# SMS account, and that stays true without a data migration.
DEFAULT_OTP_METHOD: OtpMethod = "SMS"


def parse_otp_method(stored: str | None) -> OtpMethod | None:
    """Map a stored value onto a method, or ``None`` when it is not recognised.

    Never guesses: a value the application does not know (an out-of-band database
    edit) has to fail closed at the call site rather than silently pick a channel.
    """
    if stored is None:
        return DEFAULT_OTP_METHOD
    candidate = stored.strip().upper()
    return candidate if candidate in OTP_METHODS else None  # type: ignore[return-value]


def mask_destination(method: OtpMethod, destination: str) -> str:
    """Masked destination, for the "we sent a code to …" line.

    Only ever shown right after the caller supplied the destination themselves
    (the settings/enable flow). The login flow deliberately shows no destination
    at all — see ``docs/otp-2fa-notes.md``.
    """
    if method == "EMAIL":
        return mask_email(destination)
    return mask_mobile(destination)
