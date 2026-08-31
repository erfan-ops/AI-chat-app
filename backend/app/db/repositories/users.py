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

    async def add(self, user: User) -> User:
        """Stage an insert; the caller commits."""
        self._db.add(user)
        await self._db.flush()
        return user
