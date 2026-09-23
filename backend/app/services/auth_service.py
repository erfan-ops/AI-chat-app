"""Authentication service: registration, login, and failed-login throttling."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.contact import OtpMethod
from app.core.logging import get_logger, structured
from app.core.security import PasswordManager, TokenManager
from app.core.time import utcnow
from app.db.models.user import User
from app.db.repositories.users import UserRepository
from app.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    RateLimitError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.services.otp_audit import OtpAudit
from app.services.otp_delivery import OtpDeliveryService
from app.services.otp_service import OtpService

logger = get_logger("app.services.auth")

ACTIVE_STATUS = "ACTIVE"
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60.0


class LoginAttemptTracker:
    """In-memory per-username failed-login throttle.

    Bound to a single process/event loop, so no locking is needed. A multi-worker
    production deployment should replace this with a shared store (e.g. Redis).
    """

    def __init__(
        self,
        *,
        max_attempts: int = MAX_FAILED_ATTEMPTS,
        lockout_seconds: float = LOCKOUT_SECONDS,
    ) -> None:
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, username: str, now: float) -> deque[float]:
        attempts = self._failures[username]
        cutoff = now - self._lockout_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts

    def record_failure(self, username: str) -> None:
        self._prune(username, time.monotonic()).append(time.monotonic())

    def is_locked(self, username: str) -> bool:
        return len(self._prune(username, time.monotonic())) >= self._max_attempts

    def reset(self, username: str) -> None:
        self._failures.pop(username, None)

    def reset_all(self) -> None:
        self._failures.clear()


@dataclass(frozen=True)
class LoginResult:
    user: User
    access_token: str
    expires_in_minutes: int


@dataclass(frozen=True)
class OtpChallengeResult:
    """The password was correct, but a code must be verified before a token exists.

    ``method`` says which channel took the code and ``alternative_method`` names the
    other one *when it is actually usable* — so the client can offer a switch that
    is guaranteed to work, and never has to know a contact address to do it.
    """

    challenge_id: str
    expires_in_seconds: int
    method: OtpMethod
    alternative_method: OtpMethod | None = None


class AuthService:
    def __init__(
        self,
        settings: Settings,
        *,
        passwords: PasswordManager | None = None,
        tokens: TokenManager | None = None,
        attempts: LoginAttemptTracker | None = None,
    ) -> None:
        self._settings = settings
        self._passwords = passwords or PasswordManager()
        self._tokens = tokens or TokenManager(
            settings.jwt_secret, settings.jwt_algorithm, settings.access_token_expire_minutes
        )
        self._attempts = attempts or LoginAttemptTracker()
        # Verified against when the username does not exist, so unknown-user and
        # wrong-password logins take roughly the same time.
        self._dummy_hash = self._passwords.hash("dummy-password-for-timing")

    def reset_attempts(self) -> None:
        """Clear throttle state (tests)."""
        self._attempts.reset_all()

    async def register(
        self,
        db: AsyncSession,
        *,
        username: str,
        password: str,
        display_name: str | None,
    ) -> User:
        """Create an account with an Argon2id-hashed password."""
        repo = UserRepository(db)
        if await repo.get_by_username(username) is not None:
            raise ConflictError("Username is already taken")
        now = utcnow()
        user = User(
            username=username,
            password_hash=self._passwords.hash(password),
            display_name=display_name,
            status=ACTIVE_STATUS,
            created_at=now,
            updated_at=now,
        )
        await repo.add(user)
        await db.commit()
        await db.refresh(user)
        structured(logger, logging.INFO, "user registered", user_id=user.id, username=username)
        return user

    async def login(
        self,
        db: AsyncSession,
        *,
        username: str,
        password: str,
        delivery: OtpDeliveryService,
        otp: OtpService,
        audit: OtpAudit,
    ) -> LoginResult | OtpChallengeResult:
        """Authenticate a user, or start the second step when 2FA is enabled."""
        if self._attempts.is_locked(username):
            raise RateLimitError("Too many failed login attempts; try again later")
        user = await UserRepository(db).get_by_username(username)
        password_ok = self._passwords.verify(
            user.password_hash if user else self._dummy_hash, password
        )
        if user is None or not password_ok:
            self._attempts.record_failure(username)
            structured(logger, logging.WARNING, "login failed", username=username)
            raise UnauthorizedError("Incorrect username or password")
        if user.status != ACTIVE_STATUS:
            structured(logger, logging.WARNING, "login rejected: account disabled", user_id=user.id)
            raise ForbiddenError("Account is disabled")
        self._attempts.reset(username)
        if user.otp_enabled == 1:
            # No token, no last_login_at, no commit: the password was right, but
            # nothing is authenticated until the code is verified.
            return await self._start_otp_login(user, delivery=delivery, otp=otp, audit=audit)
        user.last_login_at = utcnow()
        await db.commit()
        token, expires_in = self._tokens.create_access_token(user.id)
        structured(logger, logging.INFO, "login succeeded", user_id=user.id)
        return LoginResult(user=user, access_token=token, expires_in_minutes=expires_in)

    async def complete_otp_login(
        self,
        db: AsyncSession,
        *,
        challenge_id: str,
        code: str,
        otp: OtpService,
        audit: OtpAudit,
    ) -> LoginResult:
        """Second step of a two-step login: consume the code, then issue the token."""
        verified = otp.verify(challenge_id=challenge_id, code=code, purpose="login")
        user = await UserRepository(db).get_by_id(verified.user_id)
        if user is None or user.status != ACTIVE_STATUS:
            raise ForbiddenError("Account is disabled")
        if user.otp_enabled != 1:
            # Two-step verification was turned off between the two steps.
            raise BadRequestError("Two-step verification is no longer enabled")
        user.last_login_at = utcnow()
        await db.commit()
        token, expires_in = self._tokens.create_access_token(user.id)
        structured(logger, logging.INFO, "login succeeded", user_id=user.id, second_factor=True)
        # After the commit: the login is what matters, the history records it.
        await audit.consumed(user_id=user.id, purpose="login")
        return LoginResult(user=user, access_token=token, expires_in_minutes=expires_in)

    async def _start_otp_login(
        self,
        user: User,
        *,
        delivery: OtpDeliveryService,
        otp: OtpService,
        audit: OtpAudit,
        method: OtpMethod | None = None,
        replaces: str | None = None,
    ) -> OtpChallengeResult:
        """Send the login code to ``method`` (default: the saved preference).

        Fails closed when the account cannot be reached that way: falling back to
        the other channel would quietly send a code wherever the user did not ask
        for it, and dropping to password-only would drop the second factor.

        ``replaces`` names the challenge this one supersedes (a method switch); it
        is only spent once the new code is in flight, so a provider failure leaves
        the code the user already holds working.
        """
        chosen = method or delivery.default_method(user)
        if chosen is None or delivery.destination_for(user, chosen) is None:
            # Reachable only by editing the database, but it must not fall through
            # to a password-only login — that would silently drop the second factor.
            structured(
                logger,
                logging.ERROR,
                "otp enabled without a usable destination",
                user_id=user.id,
                method=chosen,
            )
            raise ServiceUnavailableError("Two-step verification is unavailable for this account")

        destination = delivery.resolve(user, chosen)
        challenge_id, code = otp.claim(
            user_id=user.id, purpose="login", method=chosen, destination=destination.address
        )
        try:
            await delivery.send(destination, code=code, user=user, purpose="login")
        except ServiceUnavailableError:
            # Let the user retry immediately when the provider is the problem.
            otp.discard(challenge_id, user_id=user.id)
            raise
        otp.commit(challenge_id, replaces=replaces)
        # The code is on its way, so it belongs in the history.
        await audit.issued(
            challenge=otp.require_live(challenge_id, purpose="login"),
            method=chosen,
            ttl_seconds=otp.code_ttl_seconds,
        )
        return OtpChallengeResult(
            challenge_id=challenge_id,
            expires_in_seconds=self._settings.otp_code_ttl_seconds,
            method=chosen,
            alternative_method=delivery.alternative_method(user, chosen),
        )

    async def switch_otp_method(
        self,
        db: AsyncSession,
        *,
        challenge_id: str,
        method: OtpMethod,
        delivery: OtpDeliveryService,
        otp: OtpService,
        audit: OtpAudit,
    ) -> OtpChallengeResult:
        """Send the login code through ``method`` instead, for this login only.

        The saved preference is never touched — this is a per-login choice. The
        challenge the caller is holding is only invalidated once the replacement is
        actually in flight, so a switch that fails (no destination, provider down,
        cooldown) leaves their existing code working.
        """
        pending = otp.require_live(challenge_id, purpose="login")
        user = await UserRepository(db).get_by_id(pending.user_id)
        if user is None or user.status != ACTIVE_STATUS:
            raise ForbiddenError("Account is disabled")
        if user.otp_enabled != 1:
            raise BadRequestError("Two-step verification is no longer enabled")

        return await self._start_otp_login(
            user, delivery=delivery, otp=otp, audit=audit, method=method, replaces=challenge_id
        )
