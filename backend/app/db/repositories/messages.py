"""Data access for MESSAGES and MESSAGE_GENERATIONS."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.message import Message, MessageGeneration


class MessageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_conversation(
        self,
        conversation_id: int,
        *,
        limit: int,
        before_id: int | None = None,
    ) -> list[Message]:
        """Newest-first fetch with an optional id cursor, returned in chronological order."""
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        if before_id is not None:
            stmt = stmt.where(Message.id < before_id)
        rows = list((await self._db.scalars(stmt.order_by(Message.id.desc()).limit(limit))).all())
        rows.reverse()
        return rows

    async def get_for_conversation(self, message_id: int, conversation_id: int) -> Message | None:
        stmt = select(Message).where(
            Message.id == message_id, Message.conversation_id == conversation_id
        )
        return (await self._db.scalars(stmt)).first()

    async def get_many_for_conversation(
        self, message_ids: Collection[int], conversation_id: int
    ) -> list[Message]:
        """Batch fetch by primary keys, scoped to one conversation (reply targets).

        One ``IN`` query instead of a query per referenced message.
        """
        stmt = select(Message).where(
            Message.conversation_id == conversation_id, Message.id.in_(message_ids)
        )
        return list((await self._db.scalars(stmt)).all())

    async def add(self, message: Message) -> Message:
        self._db.add(message)
        await self._db.flush()
        return message


class GenerationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, generation: MessageGeneration) -> MessageGeneration:
        self._db.add(generation)
        await self._db.flush()
        return generation
