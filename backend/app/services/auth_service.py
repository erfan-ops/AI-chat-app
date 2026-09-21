"""Authentication service: registration, login, and failed-login throttling."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
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
from app.services.otp_service import OtpService
from app.services.sms_service import SmsService

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
    """The password was correct, but a code must be verified before a token exists."""

    challenge_id: str
    expires_in_seconds: int


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
        sms: SmsService,
        otp: OtpService,
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
            return await self._start_otp_login(user, sms=sms, otp=otp)
        user.last_login_at = utcnow()
        await db.commit()
        token, expires_in = self._tokens.create_access_token(user.id)
        structured(logger, logging.INFO, "login succeeded", user_id=user.id)
        return LoginResult(user=user, access_token=token, expires_in_minutes=expires_in)

    async def complete_otp_login(
        self, db: AsyncSession, *, challenge_id: str, code: str, otp: OtpService
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
        return LoginResult(user=user, access_token=token, expires_in_minutes=expires_in)

    async def _start_otp_login(
        self, user: User, *, sms: SmsService, otp: OtpService
    ) -> OtpChallengeResult:
        """Send the login code, or fail closed if the account is inconsistent."""
        if not user.mobile_number:
            # Reachable only by editing the database, but it must not fall through
            # to a password-only login — that would silently drop the second factor.
            structured(
                logger, logging.ERROR, "otp enabled without a mobile number", user_id=user.id
            )
            raise ServiceUnavailableError("Two-step verification is unavailable for this account")
        challenge_id, code = otp.issue(user_id=user.id, purpose="login")
        try:
            await sms.send_verify_code(
                mobile=f"{user.mobile_number:010d}",
                code=code,
                display_name=user.display_name,
                username=user.username,
                user_id=user.id,
            )
        except ServiceUnavailableError:
            # Let the user retry immediately when the provider is the problem.
            otp.discard(challenge_id, user_id=user.id)
            raise
        return OtpChallengeResult(
            challenge_id=challenge_id,
            expires_in_seconds=self._settings.otp_code_ttl_seconds,
        )
