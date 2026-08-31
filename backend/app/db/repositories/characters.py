"""Data access for CHARACTERS.

Visibility filtering happens here: a user sees the built-in characters
(``OWNER_USER_ID IS NULL``) plus the ones they created themselves, never
another user's private character.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.character import Character


def _visible_to(user_id: int) -> ColumnElement[bool]:
    """Built-in characters plus the user's own."""
    return or_(Character.owner_user_id.is_(None), Character.owner_user_id == user_id)


class CharacterRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_visible(self, user_id: int) -> list[Character]:
        stmt = (
            select(Character)
            .where(Character.status == "ACTIVE", _visible_to(user_id))
            .order_by(Character.id)
        )
        return list((await self._db.scalars(stmt)).all())

    async def list_all(self) -> list[Character]:
        """Every character, any owner and any status (administrators only)."""
        return list((await self._db.scalars(select(Character).order_by(Character.id))).all())

    async def get_visible(self, character_id: int, user_id: int) -> Character | None:
        stmt = select(Character).where(
            Character.id == character_id,
            Character.status == "ACTIVE",
            _visible_to(user_id),
        )
        return (await self._db.scalars(stmt)).first()

    async def get_by_id(self, character_id: int) -> Character | None:
        stmt = select(Character).where(Character.id == character_id)
        return (await self._db.scalars(stmt)).first()

    async def add(self, character: Character) -> Character:
        self._db.add(character)
        await self._db.flush()
        return character
