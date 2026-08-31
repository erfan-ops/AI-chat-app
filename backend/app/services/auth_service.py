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
from app.exceptions import ConflictError, ForbiddenError, RateLimitError, UnauthorizedError

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


class AuthService:
    def __init__(
        self,
        settings: Settings,
        *,
        passwords: PasswordManager | None = None,
        tokens: TokenManager | None = None,
        attempts: LoginAttemptTracker | None = None,
    ) -> None:
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

    async def login(self, db: AsyncSession, *, username: str, password: str) -> LoginResult:
        """Authenticate a user and issue an access token."""
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
        user.last_login_at = utcnow()
        await db.commit()
        token, expires_in = self._tokens.create_access_token(user.id)
        structured(logger, logging.INFO, "login succeeded", user_id=user.id)
        return LoginResult(user=user, access_token=token, expires_in_minutes=expires_in)
