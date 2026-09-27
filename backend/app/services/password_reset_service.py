"""Password recovery: prove control of the account, then change the password once.

Three steps, and the middle one is what makes the last one safe. The API verifies the
code itself and mints a short-lived authorization bound to that account; the password
is only ever changed for a caller holding one. The client is never asked whether the
code was right — a request that claimed it would be worth nothing, because the only
thing that authorizes a change is a token this module created and still remembers.

The recovery channel is whichever the account already uses: a code by SMS or email
through the existing challenge store (``purpose="password_reset"``, so its counters are
its own), or a code from the user's *existing* authenticator — the same
``USERS.TOTP_SECRET`` that signs them in, never a second secret and never a rotation.

State lives in this process, like the challenge store and the login throttle, and for
the same reason: the schema has no table for it and the application never alters
tables. The documented consequence is the same as well — single worker or sticky
sessions, and a restart loses outstanding resets (see docs/password-reset-notes.md).
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.contact import OTP_METHODS, OtpMethod, Purpose
from app.core.email import normalize_email
from app.core.logging import get_logger, structured
from app.core.security import PasswordManager
from app.core.time import utcnow
from app.db.models.user import User
from app.db.repositories.users import UserRepository
from app.exceptions import BadRequestError, NotFoundError
from app.services.otp_audit import OtpAudit
from app.services.otp_delivery import OtpDeliveryService
from app.services.otp_service import OtpService
from app.services.totp_service import TotpService, totp_validator

logger = get_logger("app.services.password_reset")

# The purpose every challenge, every OTP_LOG row and every limit group of this flow
# carries. It is what keeps a recovery code from being spent on a login and a login
# code from being spent on a recovery.
PURPOSE: Purpose = "password_reset"

# What a request answers with when the account does not exist, or cannot be reached at
# all: the same shape, and the same list, as an account that has every method set up.
# The response must not be a way to ask "does this account exist?".
_UNAVAILABLE_METHODS: tuple[OtpMethod, ...] = OTP_METHODS

_MESSAGE = "If that account can be recovered, a verification code has been sent."


@dataclass(frozen=True)
class RecoveryOptions:
    """What the requester may do next, and never more than that.

    ``method``/``challenge_id`` are present only when a code actually went out; the
    masked destination is deliberately *not* here, because the caller of this endpoint
    has proved nothing about the account yet and must not learn a phone suffix or part
    of an address from it.
    """

    methods: tuple[OtpMethod, ...]
    method: OtpMethod | None = None
    challenge_id: str | None = None
    code_expires_in_seconds: int | None = None
    message: str = _MESSAGE


@dataclass(frozen=True)
class ResetAuthorization:
    """The proof that the code was verified, for the next ten minutes."""

    token: str
    expires_in_seconds: int


@dataclass(frozen=True)
class _Authorization:
    """One issued authorization: whose it is, and when it stops being usable."""

    user_id: int
    expires_at: float


class PasswordResetService:
    """Issues recovery codes, verifies them, and authorizes one password change."""

    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._clock = clock
        # Per-process key for token hashing, exactly like the challenge store's: what is
        # kept is an HMAC of the token, so a dump of this process is not a list of usable
        # resets, and a restart invalidates them (they last ten minutes).
        self._hash_key = secrets.token_bytes(32)
        self._authorizations: dict[bytes, _Authorization] = {}

    # -- internals -----------------------------------------------------------------

    @property
    def authorization_ttl_seconds(self) -> int:
        return self._settings.password_reset_authorization_ttl_seconds

    def _digest(self, token: str) -> bytes:
        return hmac.new(self._hash_key, token.encode(), sha256).digest()

    def _prune(self, now: float) -> None:
        for digest, authorization in list(self._authorizations.items()):
            if authorization.expires_at <= now:
                del self._authorizations[digest]

    @staticmethod
    async def _find_user(db: AsyncSession, identifier: str) -> User | None:
        """The account a recovery attempt names, by username or by email.

        Usernames are the application's own identifier and are compared exactly (as
        everywhere else); an email is canonicalised first, because that is how it is
        stored. Anything else simply finds nothing.
        """
        text = identifier.strip()
        if not text:
            return None
        repo = UserRepository(db)
        user = await repo.get_by_username(text)
        if user is None and "@" in text:
            try:
                user = await repo.get_by_email(normalize_email(text))
            except BadRequestError:
                # Not an address this application would ever have stored.
                return None
        return user

    @staticmethod
    def _available_methods(user: User, delivery: OtpDeliveryService) -> tuple[OtpMethod, ...]:
        """The methods that can actually produce a code for this account right now.

        ``OtpDeliveryService.usable`` is the same question the sign-in flow asks, so an
        account's recovery options and its sign-in options cannot drift apart: a
        verified contact with a configured provider, or an enrolled authenticator.
        """
        return tuple(method for method in OTP_METHODS if delivery.usable(user, method))

    # -- step 1: identify, and optionally send --------------------------------------

    async def request(
        self,
        db: AsyncSession,
        *,
        identifier: str,
        method: OtpMethod | None,
        delivery: OtpDeliveryService,
        otp: OtpService,
        audit: OtpAudit,
        client_ip: str | None = None,
    ) -> RecoveryOptions:
        """Report the account's recovery methods, and send a code for ``method``.

        With no ``method`` this only reports — that is the "which account is this?"
        step, and it sends nothing. With one, a code goes out through the existing
        challenge machinery and the same generic answer comes back whether or not it
        could be sent, so the response is never an oracle.
        """
        # First, and before anything is looked up: every recovery request counts
        # against the caller's address window. Counting only the sends would make 429
        # itself the oracle — see OtpService.reserve_request_slot.
        otp.reserve_request_slot(client_ip=client_ip)
        user = await self._find_user(db, identifier)
        if user is None or not user.is_active:
            structured(logger, logging.INFO, "password reset requested", found=False)
            return RecoveryOptions(methods=_UNAVAILABLE_METHODS)

        available = self._available_methods(user, delivery)
        if method is None:
            return RecoveryOptions(methods=available or _UNAVAILABLE_METHODS)
        if method not in available:
            # The account exists but cannot be reached that way. Nothing is sent and the
            # answer is the same as for an unknown account: saying so would turn this
            # endpoint into a way to map accounts to their contacts.
            structured(
                logger,
                logging.INFO,
                "password reset requested",
                found=True,
                method=method,
                available=len(available),
            )
            return RecoveryOptions(methods=available or _UNAVAILABLE_METHODS)

        if method == "TOTP":
            challenge_id = otp.claim_totp(user_id=user.id, purpose=PURPOSE)
            otp.commit(challenge_id)
            await audit.issued(
                challenge=otp.require_live(challenge_id, purpose=PURPOSE),
                method="TOTP",
                ttl_seconds=otp.code_ttl_seconds,
            )
            return RecoveryOptions(
                methods=available,
                method="TOTP",
                challenge_id=challenge_id,
                code_expires_in_seconds=otp.code_ttl_seconds,
            )

        destination = delivery.resolve(user, method)
        # No ``client_ip`` here on purpose: this request has already been counted
        # against that window (above), and a send must not be charged for it twice.
        challenge_id, code = otp.claim(
            user_id=user.id,
            purpose=PURPOSE,
            method=method,
            destination=destination.address,
        )
        try:
            await delivery.send(destination, code=code, user=user, purpose=PURPOSE)
        except Exception:
            # Nothing left the building, so let the user try again immediately — and
            # keep the failure to ourselves, like every other answer here.
            otp.discard(challenge_id, user_id=user.id)
            raise
        otp.commit(challenge_id)
        await audit.issued(
            challenge=otp.require_live(challenge_id, purpose=PURPOSE),
            method=method,
            ttl_seconds=otp.code_ttl_seconds,
        )
        return RecoveryOptions(
            methods=available,
            method=method,
            challenge_id=challenge_id,
            code_expires_in_seconds=otp.code_ttl_seconds,
        )

    # -- step 2: verify the code, and authorize one change -------------------------

    async def verify(
        self,
        db: AsyncSession,
        *,
        challenge_id: str,
        code: str,
        otp: OtpService,
        totp: TotpService,
        audit: OtpAudit,
    ) -> ResetAuthorization:
        """Consume the recovery challenge and mint the authorization.

        Nothing about the account is returned: the caller gets a token and how long it
        lasts. The token is not a session — it opens one endpoint, once.
        """
        pending = otp.require_live(challenge_id, purpose=PURPOSE)
        validator = None
        if pending.method == "TOTP":
            owner = await UserRepository(db).get_by_id(pending.user_id)
            validator = totp_validator(owner, totp)
        verified = otp.verify(
            challenge_id=challenge_id, code=code, purpose=PURPOSE, validator=validator
        )

        user = await UserRepository(db).get_by_id(verified.user_id)
        if user is None or not user.is_active:
            raise NotFoundError("User not found")

        now = self._clock()
        self._prune(now)
        token = secrets.token_urlsafe(32)
        self._authorizations[self._digest(token)] = _Authorization(
            user_id=user.id, expires_at=now + self.authorization_ttl_seconds
        )
        structured(
            logger,
            logging.INFO,
            "password reset authorized",
            user_id=user.id,
            method=verified.method,
            expires_in_seconds=self.authorization_ttl_seconds,
        )
        # The code has been used: the history records it, best effort.
        await audit.consumed(user_id=user.id, purpose=PURPOSE)
        return ResetAuthorization(token=token, expires_in_seconds=self.authorization_ttl_seconds)

    # -- step 3: change the password -----------------------------------------------

    async def complete(
        self,
        db: AsyncSession,
        *,
        token: str,
        new_password: str,
        passwords: PasswordManager,
        otp: OtpService,
    ) -> User:
        """Set the new password, for the account the authorization was issued to.

        The authorization is spent *before* the write, so a token can never be used
        twice even if two requests arrive together: the second finds nothing. The
        consequence of a failure after this point is that the user starts again, which
        is the right trade — the alternative is a stolen token racing the real owner.
        """
        user_id = self._consume(token)

        user = await UserRepository(db).get_by_id(user_id)
        if user is None or not user.is_active:
            raise NotFoundError("User not found")

        user.password_hash = passwords.hash(new_password)
        user.updated_at = utcnow()
        await db.commit()
        await db.refresh(user)

        # Nothing that was in flight may authorize anything now: the password just
        # changed, so outstanding sign-in codes (and anything else this account had
        # open) are dropped, along with any other authorization for it.
        otp.invalidate_user(user.id)
        self.invalidate_user(user.id)
        structured(logger, logging.INFO, "password reset completed", user_id=user.id)
        return user

    def _consume(self, token: str) -> int:
        """Spend an authorization: the user id it was issued for, or a 400."""
        now = self._clock()
        self._prune(now)
        authorization = self._authorizations.pop(self._digest(token), None)
        if authorization is None:
            structured(logger, logging.WARNING, "password reset refused: unknown authorization")
            raise BadRequestError(
                "That password reset is no longer valid. Request a new code to continue."
            )
        return authorization.user_id

    def invalidate_user(self, user_id: int) -> None:
        """Drop every outstanding authorization for one account."""
        for digest, authorization in list(self._authorizations.items()):
            if authorization.user_id == user_id:
                del self._authorizations[digest]

    def reset_all(self) -> None:
        """Clear all state (tests)."""
        self._authorizations.clear()
