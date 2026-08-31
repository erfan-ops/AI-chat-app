"""Data access for CONVERSATIONS.

Ownership filtering happens here (``user_id`` in every query) so that authorization
is enforced at the data-access layer, not just in route handlers.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.conversation import Conversation


class ConversationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_user(self, user_id: int, *, limit: int, offset: int) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.status == "ACTIVE")
            .options(
                selectinload(Conversation.character),
                selectinload(Conversation.model),
                selectinload(Conversation.user_persona),
            )
            .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self._db.scalars(stmt)).all())

    async def get_active_for_user(self, conversation_id: int, user_id: int) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.status == "ACTIVE",
        )
        return (await self._db.scalars(stmt)).first()

    async def get_active_for_user_detailed(
        self, conversation_id: int, user_id: int
    ) -> Conversation | None:
        """Owned, active conversation with character + model eagerly loaded."""
        stmt = (
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.status == "ACTIVE",
            )
            .options(
                selectinload(Conversation.character),
                selectinload(Conversation.model),
                selectinload(Conversation.user_persona),
            )
        )
        return (await self._db.scalars(stmt)).first()

    async def add(self, conversation: Conversation) -> Conversation:
        self._db.add(conversation)
        await self._db.flush()
        return conversation
