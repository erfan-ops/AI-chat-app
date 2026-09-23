"""One-time verification codes for two-step login and contact verification.

Deliberately in-process state, mirroring ``LoginAttemptTracker``: the schema has
no OTP table and the application never alters tables. That means, exactly like the
login throttle, **this is a single-process store** — a multi-worker deployment
needs a shared store (e.g. Redis) or sticky sessions, otherwise a code issued by
one worker is unknown to the next.

Codes are never stored or logged in plaintext: only an HMAC of the code is kept,
and the comparison is constant-time. A challenge is bound to one user and one
purpose, expires after ``otp_code_ttl_seconds``, is single-use, and dies after
``otp_max_verify_attempts`` wrong guesses.

Nothing here knows how a code travels: a challenge records the *method* it was
issued for and the *destination* it went to, and the caller picks the provider
(``app/services/otp_delivery.py``). Switching method mid-login is therefore just
another ``claim()`` — the security controls stay in one place.

Issuing is two-phase on purpose. ``claim()`` reserves the send slot and mints the
code; ``commit()`` installs it once the provider has accepted the message. Between
the two the caller does its I/O, so a send that fails can be written off with
``discard()`` without ever having disturbed the code the user is already holding.
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

from app.core.config import Settings
from app.core.contact import DEFAULT_OTP_METHOD, OtpMethod, Purpose
from app.core.logging import get_logger, structured
from app.exceptions import BadRequestError, RateLimitError

logger = get_logger("app.services.otp")

OTP_CODE_DIGITS = 6
_PER_DAY_SECONDS = 24 * 60 * 60

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
    # The method the code was sent by, and where it went. The enable flow persists
    # the destination that was actually verified, never a second client-supplied
    # value; the login flow carries it so a switch knows which contact it is
    # moving between.
    method: OtpMethod = DEFAULT_OTP_METHOD
    destination: str | None = None


@dataclass(frozen=True)
class VerifiedChallenge:
    """What a successfully consumed challenge proves."""

    user_id: int
    purpose: Purpose
    method: OtpMethod
    destination: str | None


class OtpService:
    """Issues and verifies one-time codes. Bound to one process."""

    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._clock = clock
        # Per-process key for code hashing; codes are short-lived, so a restart
        # simply invalidates the outstanding ones.
        self._hash_key = secrets.token_bytes(32)
        self._challenges: dict[str, Challenge] = {}
        # Claimed but not yet sent: a challenge only becomes live once the provider
        # has accepted the message (see claim/commit), so a failed send cannot take
        # away a code the user already has.
        self._pending: dict[str, Challenge] = {}
        # Cooldown and daily budget are keyed differently on purpose: the cooldown
        # is per (user, method) so switching to the other channel is immediate,
        # while the daily cap stays per user — switching must not buy extra sends.
        self._last_send: dict[tuple[int, OtpMethod], float] = {}
        self._sends: dict[int, deque[float]] = {}

    # -- internals ---------------------------------------------------------------

    def _prune(self, now: float) -> None:
        """Drop expired challenges and stale send history (no lookups create state)."""
        for store in (self._challenges, self._pending):
            for challenge_id in [key for key, value in store.items() if value.expires_at <= now]:
                del store[challenge_id]
        cutoff = now - _PER_DAY_SECONDS
        for user_id, sends in list(self._sends.items()):
            while sends and sends[0] < cutoff:
                sends.popleft()
            if not sends:
                del self._sends[user_id]
        # A cooldown that has already elapsed can never block anything again, so the
        # entry is dead weight — and this dict is the one that grows with users.
        cooldown = self._settings.otp_resend_cooldown_seconds
        for key, sent_at in list(self._last_send.items()):
            if now - sent_at >= cooldown:
                del self._last_send[key]

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

    def claim(
        self,
        *,
        user_id: int,
        purpose: Purpose,
        method: OtpMethod = DEFAULT_OTP_METHOD,
        destination: str | None = None,
    ) -> tuple[str, str]:
        """Reserve a send slot and mint a code. Returns ``(challenge_id, code)``.

        The challenge is held aside until ``commit``: sending is the caller's next
        step, and a provider that refuses must not cost the user the code they are
        already holding. Everything is validated before any state changes, so a
        rejected claim leaves the previous challenge untouched.

        Synchronous on purpose: the cooldown is claimed here, *before* the caller
        awaits the provider, so two concurrent requests cannot both send.
        """
        now = self._clock()
        self._prune(now)

        cooldown = self._settings.otp_resend_cooldown_seconds
        last_send = self._last_send.get((user_id, method))
        if last_send is not None and now - last_send < cooldown:
            wait = int(cooldown - (now - last_send)) + 1
            raise RateLimitError(
                "Please wait before requesting another code", retry_after_seconds=wait
            )

        # The deque has to be able to hold a full day's allowance, or it would evict
        # from the left and the cap below could never be reached.
        sends = self._sends.setdefault(
            user_id, deque(maxlen=max(_MAX_DAILY_HISTORY, self._settings.otp_max_sends_per_day))
        )
        if len(sends) >= self._settings.otp_max_sends_per_day:
            raise RateLimitError(
                "Too many codes requested today; try again tomorrow",
                retry_after_seconds=_PER_DAY_SECONDS,
            )

        code = f"{secrets.randbelow(10**OTP_CODE_DIGITS):0{OTP_CODE_DIGITS}d}"
        challenge_id = secrets.token_urlsafe(32)
        self._pending[challenge_id] = Challenge(
            user_id=user_id,
            purpose=purpose,
            code_hash=self._hash(challenge_id, code),
            expires_at=now + self._settings.otp_code_ttl_seconds,
            attempts_left=self._settings.otp_max_verify_attempts,
            method=method,
            destination=destination,
        )
        self._last_send[(user_id, method)] = now
        sends.append(now)
        structured(
            logger,
            logging.INFO,
            "otp issued",
            user_id=user_id,
            purpose=purpose,
            method=method,
        )
        return challenge_id, code

    def commit(self, challenge_id: str, *, replaces: str | None = None) -> None:
        """Install a claimed challenge, now that the provider has accepted the send.

        Installing invalidates whatever was outstanding for that user, so at most one
        code is live per user — but only *now*, once a code has actually gone out.
        Nothing the user is already holding is touched before this point, which is
        what makes a failed send survivable.

        ``replaces`` is the challenge the caller believed was live (the one a method
        switch is moving away from). If it has already gone, another request got
        there first and the code just sent is not the one that will be accepted, so
        the caller is refused rather than handed a challenge id that is already dead.
        """
        pending = self._pending.pop(challenge_id, None)
        if pending is None or (replaces is not None and self._challenges.get(replaces) is None):
            raise BadRequestError(_CHALLENGE_GONE_MESSAGE)
        self._clear_user_challenges(pending.user_id)
        self._challenges[challenge_id] = pending

    def discard(self, challenge_id: str, *, user_id: int) -> None:
        """Drop a challenge whose send never left (and let the user retry at once).

        Covers both a challenge that was never committed (the usual case) and one
        that was. A challenge belonging to someone else is left alone entirely — the
        ownership check comes *before* anything is removed, so a foreign id cannot
        destroy another user's code. Only the failed challenge's own method is
        released, so a provider outage on one channel cannot reset the other's
        cooldown. The daily cap is intentionally *not* decremented, so a failing
        provider cannot be used to send without bound.
        """
        challenge = self._pending.get(challenge_id) or self._challenges.get(challenge_id)
        if challenge is None or challenge.user_id != user_id:
            return
        self._pending.pop(challenge_id, None)
        self._challenges.pop(challenge_id, None)
        self._last_send.pop((user_id, challenge.method), None)

    def require_live(self, challenge_id: str, *, purpose: Purpose) -> Challenge:
        """Read a live challenge without consuming it, or raise ``BadRequestError``.

        Used by the method switch, which must only touch the challenge once the
        replacement is known to be issuable — consuming first would leave the user
        with no code at all if the new ``issue()`` were rejected. An unknown, expired
        or wrong-purpose challenge is the same opaque error as everywhere else.
        """
        now = self._clock()
        self._prune(now)
        challenge = self._challenges.get(challenge_id)
        if challenge is None or challenge.purpose != purpose:
            raise BadRequestError(_CHALLENGE_GONE_MESSAGE)
        return challenge

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
            user_id=challenge.user_id,
            purpose=challenge.purpose,
            method=challenge.method,
            destination=challenge.destination,
        )

    def invalidate_user(self, user_id: int) -> None:
        """Drop every outstanding challenge for a user (e.g. two-step turned off)."""
        self._clear_user_challenges(user_id)

    def reset_all(self) -> None:
        """Clear all state (tests)."""
        self._challenges.clear()
        self._pending.clear()
        self._last_send.clear()
        self._sends.clear()
