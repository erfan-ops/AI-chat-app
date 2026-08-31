"""Password hashing and JWT token handling.

Passwords are hashed with Argon2id via ``argon2-cffi``; tokens are signed JWTs via
``PyJWT``. No custom cryptography.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


class InvalidTokenError(Exception):
    """Raised when a JWT is missing, malformed, expired, or unsigned."""


class PasswordManager:
    """Argon2id password hashing (argon2-cffi defaults are Argon2id)."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        """Hash a plaintext password into an Argon2id PHC string."""
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """Verify a password against a stored hash; ``False`` on any mismatch."""
        try:
            return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            # Includes malformed legacy hashes — fail closed.
            return False


class TokenManager:
    """Creates and validates signed access tokens (JWT, HS256 by default)."""

    def __init__(self, secret: str, algorithm: str, expire_minutes: int) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._expire_minutes = expire_minutes

    def create_access_token(self, user_id: int) -> tuple[str, int]:
        """Return ``(token, expires_in_minutes)``. Carries only the user id."""
        now = datetime.now(UTC)
        payload = {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=self._expire_minutes),
            "type": "access",
        }
        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        return token, self._expire_minutes

    def decode_user_id(self, token: str) -> int:
        """Validate signature/expiry and return the subject user id."""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"require": ["sub", "exp", "iat"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("Invalid or expired token") from exc
        if payload.get("type") != "access":
            raise InvalidTokenError("Invalid token type")
        try:
            return int(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidTokenError("Invalid token payload") from exc
