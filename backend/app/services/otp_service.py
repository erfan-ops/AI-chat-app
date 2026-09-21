"""One-time verification codes for two-step login and mobile verification.

Deliberately in-process state, mirroring ``LoginAttemptTracker``: the schema has
no OTP table and the application never alters tables. That means, exactly like the
login throttle, **this is a single-process store** — a multi-worker deployment
needs a shared store (e.g. Redis) or sticky sessions, otherwise a code issued by
one worker is unknown to the next.

Codes are never stored or logged in plaintext: only an HMAC of the code is kept,
and the comparison is constant-time. A challenge is bound to one user and one
purpose, expires after ``otp_code_ttl_seconds``, is single-use, and dies after
``otp_max_verify_attempts`` wrong guesses.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from app.core.config import Settings
from app.core.logging import get_logger, structured
from app.exceptions import BadRequestError, RateLimitError

logger = get_logger("app.services.otp")

OTP_CODE_DIGITS = 6
_PER_DAY_SECONDS = 24 * 60 * 60

Purpose = Literal["login", "enable"]

# Requests older than this are not retained at all.
_MAX_DAILY_HISTORY = 64

# One message for every failure mode that would otherwise tell an attacker which
# part of the guess was wrong.
_INVALID_CODE_MESSAGE = "That verification code is incorrect or has expired"
_CHALLENGE_GONE_MESSAGE = "That verification code has expired. Request a new one"


@dataclass
class Challenge:
    """One outstanding verification attempt."""

    user_id: int
    purpose: Purpose
    code_hash: bytes
    expires_at: float
    attempts_left: int
    # The number the code went to. Stored so the enable flow persists the number
    # that was actually verified, never a second client-supplied value.
    mobile: str | None = None


@dataclass(frozen=True)
class VerifiedChallenge:
    """What a successfully consumed challenge proves."""

    user_id: int
    purpose: Purpose
    mobile: str | None


class OtpService:
    """Issues and verifies one-time codes. Bound to one process."""

    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._clock = clock
        # Per-process key for code hashing; codes are short-lived, so a restart
        # simply invalidates the outstanding ones.
        self._hash_key = secrets.token_bytes(32)
        self._challenges: dict[str, Challenge] = {}
        self._last_send: dict[int, float] = {}
        self._sends: dict[int, deque[float]] = {}

    # -- internals ---------------------------------------------------------------

    def _prune(self, now: float) -> None:
        """Drop expired challenges and stale send history (no lookups create state)."""
        for challenge_id in [
            key for key, value in self._challenges.items() if value.expires_at <= now
        ]:
            del self._challenges[challenge_id]
        cutoff = now - _PER_DAY_SECONDS
        for user_id, sends in list(self._sends.items()):
            while sends and sends[0] < cutoff:
                sends.popleft()
            if not sends:
                del self._sends[user_id]

    def _hash(self, challenge_id: str, code: str) -> bytes:
        return hmac.new(self._hash_key, f"{challenge_id}:{code}".encode(), sha256).digest()

    def _clear_user_challenges(self, user_id: int) -> None:
        for challenge_id in [
            key for key, value in self._challenges.items() if value.user_id == user_id
        ]:
            del self._challenges[challenge_id]

    # -- public API --------------------------------------------------------------

    @property
    def code_ttl_seconds(self) -> int:
        """How long an issued code stays valid."""
        return self._settings.otp_code_ttl_seconds

    def issue(
        self, *, user_id: int, purpose: Purpose, mobile: str | None = None
    ) -> tuple[str, str]:
        """Claim a send slot and mint a code. Returns ``(challenge_id, code)``.

        Synchronous on purpose: the caller may only await the SMS call *after* the
        cooldown has been claimed here, so two concurrent requests cannot both send.
        """
        now = self._clock()
        self._prune(now)

        cooldown = self._settings.otp_resend_cooldown_seconds
        last_send = self._last_send.get(user_id)
        if last_send is not None and now - last_send < cooldown:
            wait = int(cooldown - (now - last_send)) + 1
            raise RateLimitError(
                "Please wait before requesting another code", retry_after_seconds=wait
            )

        sends = self._sends.setdefault(user_id, deque(maxlen=_MAX_DAILY_HISTORY))
        if len(sends) >= self._settings.otp_max_sends_per_day:
            raise RateLimitError(
                "Too many codes requested today; try again tomorrow",
                retry_after_seconds=_PER_DAY_SECONDS,
            )

        code = f"{secrets.randbelow(10**OTP_CODE_DIGITS):0{OTP_CODE_DIGITS}d}"
        challenge_id = secrets.token_urlsafe(32)
        # Re-issuing invalidates whatever was outstanding for this user.
        self._clear_user_challenges(user_id)
        self._challenges[challenge_id] = Challenge(
            user_id=user_id,
            purpose=purpose,
            code_hash=self._hash(challenge_id, code),
            expires_at=now + self._settings.otp_code_ttl_seconds,
            attempts_left=self._settings.otp_max_verify_attempts,
            mobile=mobile,
        )
        self._last_send[user_id] = now
        sends.append(now)
        structured(logger, logging.INFO, "otp issued", user_id=user_id, purpose=purpose)
        return challenge_id, code

    def discard(self, challenge_id: str, *, user_id: int) -> None:
        """Drop a challenge whose SMS never left (and let the user retry at once).

        The daily cap is intentionally *not* decremented, so a failing provider
        cannot be used to send without bound.
        """
        challenge = self._challenges.pop(challenge_id, None)
        if challenge is not None and challenge.user_id == user_id:
            self._last_send.pop(user_id, None)

    def verify(
        self,
        *,
        challenge_id: str,
        code: str,
        purpose: Purpose,
        expected_user_id: int | None = None,
    ) -> VerifiedChallenge:
        """Consume a challenge, or raise ``BadRequestError``.

        Plain ``def`` on purpose: there is no ``await`` between reading and setting
        the consumed state, so the event loop cannot interleave two verifications of
        the same code. Callers commit to the database afterwards.
        """
        now = self._clock()
        self._prune(now)

        challenge = self._challenges.get(challenge_id)
        if challenge is None or challenge.purpose != purpose:
            raise BadRequestError(_CHALLENGE_GONE_MESSAGE)
        if expected_user_id is not None and challenge.user_id != expected_user_id:
            # Someone else's challenge (or a mismatched session) — same message, so
            # nothing about the other account is revealed.
            raise BadRequestError(_CHALLENGE_GONE_MESSAGE)

        if not hmac.compare_digest(challenge.code_hash, self._hash(challenge_id, code)):
            challenge.attempts_left -= 1
            if challenge.attempts_left <= 0:
                del self._challenges[challenge_id]
                structured(
                    logger,
                    logging.WARNING,
                    "otp attempts exhausted",
                    user_id=challenge.user_id,
                    purpose=purpose,
                )
            raise BadRequestError(_INVALID_CODE_MESSAGE)

        # Single use: consuming is removal.
        del self._challenges[challenge_id]
        structured(logger, logging.INFO, "otp verified", user_id=challenge.user_id, purpose=purpose)
        return VerifiedChallenge(
            user_id=challenge.user_id, purpose=challenge.purpose, mobile=challenge.mobile
        )

    def invalidate_user(self, user_id: int) -> None:
        """Drop every outstanding challenge for a user (e.g. two-step turned off)."""
        self._clear_user_challenges(user_id)

    def reset_all(self) -> None:
        """Clear all state (tests)."""
        self._challenges.clear()
        self._last_send.clear()
        self._sends.clear()
