"""Data access for MEMORIES (always scoped to the owning user)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.memory import Memory


class MemoryRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_user(
        self,
        user_id: int,
        *,
        character_id: int | None = None,
        limit: int,
        active_only: bool = True,
    ) -> list[Memory]:
        stmt = select(Memory).where(Memory.user_id == user_id)
        if character_id is not None:
            stmt = stmt.where(Memory.character_id == character_id)
        if active_only:
            stmt = stmt.where(Memory.status == "ACTIVE")
        rows = list(
            (await self._db.scalars(stmt.order_by(Memory.updated_at.desc()).limit(limit))).all()
        )
        return rows

    async def top_for_context(self, user_id: int, character_id: int, *, limit: int) -> list[Memory]:
        """Active memories for a character, most important first (context injection)."""
        stmt = (
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.character_id == character_id,
                Memory.status == "ACTIVE",
            )
            .order_by(Memory.importance.desc().nulls_last(), Memory.updated_at.desc())
            .limit(limit)
        )
        return list((await self._db.scalars(stmt)).all())
