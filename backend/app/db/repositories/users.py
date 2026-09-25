"""Data access for USERS."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, user_id: int) -> User | None:
        stmt = select(User).where(User.id == user_id)
        return (await self._db.scalars(stmt)).first()

    async def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        return (await self._db.scalars(stmt)).first()

    async def get_by_mobile(self, mobile: int) -> User | None:
        """The account holding that verified mobile number, if any."""
        stmt = select(User).where(User.mobile_number == mobile)
        return (await self._db.scalars(stmt)).first()

    async def get_by_google_sub(self, google_sub: str) -> User | None:
        """The account that signed in with this Google identity, if any.

        ``sub`` is Google's stable per-client identifier, and the only key a Google
        sign-in may resolve an account by — never the email address, which can be
        reassigned by a provider.
        """
        stmt = select(User).where(User.google_sub == google_sub)
        return (await self._db.scalars(stmt)).first()

    async def get_by_email(self, email: str) -> User | None:
        """The account holding that verified email address, if any.

        Compared against the canonical (lowercase) form both sides are stored in.
        """
        stmt = select(User).where(User.email == email)
        return (await self._db.scalars(stmt)).first()

    async def add(self, user: User) -> User:
        """Stage an insert; the caller commits."""
        self._db.add(user)
        await self._db.flush()
        return user
