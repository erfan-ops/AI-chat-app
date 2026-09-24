"""Authenticator-app (TOTP) codes: secrets, provisioning URIs, verification.

The parameters are the ones every authenticator app assumes — SHA1, 6 digits, a
30-second period — so a scanned entry behaves identically in Google Authenticator,
Authy, 1Password and the rest. The algorithm itself comes from ``pyotp``; nothing
here re-implements HMAC or the truncation step.

Verification is the only place the clock matters, and it comes from
``TimeService`` — this module knows nothing about NTP, only about "what time is it
according to the application's corrected clock".
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

import pyotp

from app.core.contact import OtpMethod, stored_secret
from app.core.logging import get_logger, structured
from app.exceptions import ServiceUnavailableError
from app.services.time_service import TimeService

logger = get_logger("app.services.totp")

# What an authenticator app will assume, stated explicitly rather than left to
# defaults: these values are also what the otpauth:// URI advertises.
TOTP_ISSUER = "AI-Chat"
TOTP_DIGITS = 6
TOTP_INTERVAL_SECONDS = 30
TOTP_DIGEST = hashlib.sha1
# One period either side, per RFC 6238's recommended drift allowance: the previous,
# current and next window. Wider would mean accepting codes that are a minute old.
TOTP_VALID_WINDOW = 1


def totp_validator(user: Any, totp: TotpService) -> Any:
    """A checker for that user's authenticator codes.

    Raises when no authenticator is enrolled: a TOTP challenge with no secret must
    fail closed rather than be checked against nothing.
    """
    secret = stored_secret(getattr(user, "totp_secret", None))
    if secret is None:
        raise ServiceUnavailableError("Two-step verification is unavailable for this account")
    return lambda entered: totp.verify(secret=secret, code=entered)


class TotpService:
    """Secret generation, enrolment URIs, and code verification."""

    def __init__(self, time_service: TimeService) -> None:
        self._time = time_service

    def generate_secret(self) -> str:
        """A fresh Base32 secret: 20 random bytes, which is exactly 32 characters.

        That is the width of the column, and it comes from the library's
        cryptographically secure generator — never from anything about the user.
        """
        return pyotp.random_base32()

    def provisioning_uri(self, *, secret: str, username: str) -> str:
        """The ``otpauth://`` URI an authenticator app scans.

        The issuer appears in both the label and the ``issuer`` parameter, which is
        what compatible apps expect; ``pyotp`` URL-encodes the account name, so a
        username with dots or spaces survives the round trip.
        """
        return pyotp.TOTP(
            secret,
            digits=TOTP_DIGITS,
            interval=TOTP_INTERVAL_SECONDS,
            digest=TOTP_DIGEST,
        ).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)

    def code_at(self, *, secret: str, unix_time: float) -> str:
        """The code that secret produces at a given Unix time (used by tests)."""
        return pyotp.TOTP(
            secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_SECONDS, digest=TOTP_DIGEST
        ).at(int(unix_time))

    def verify(self, *, secret: str, code: str) -> bool:
        """Whether ``code`` is valid for ``secret`` right now.

        The time comes from the application's corrected clock, and the window is the
        previous, current and next period. A clock that is not synchronized (or whose
        offset has gone stale) is refused outright rather than guessed at.
        """
        if not self._time.is_usable:
            # Worth a log of its own: "the code was wrong" and "this server has no
            # trustworthy time" look identical to the user, and only one of them is
            # the user's problem. The status separates never-synchronized from stale.
            structured(
                logger,
                logging.WARNING,
                "authenticator code refused: clock not trustworthy",
                clock_status=self._time.status,
                offset_age_seconds=(
                    None
                    if self._time.offset_age_seconds is None
                    else round(self._time.offset_age_seconds, 1)
                ),
            )
            raise ServiceUnavailableError(
                "Authenticator codes cannot be verified right now; use another method"
            )
        now = datetime.fromtimestamp(self._time.get_current_unix_time(), tz=UTC)
        return pyotp.TOTP(
            secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_SECONDS, digest=TOTP_DIGEST
        ).verify(code, for_time=now, valid_window=TOTP_VALID_WINDOW)

    @staticmethod
    def method() -> OtpMethod:
        """The delivery method these codes belong to."""
        return "TOTP"
