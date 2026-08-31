"""Message listing (ownership flows through the parent conversation)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.message import Message
from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.messages import MessageRepository
from app.exceptions import NotFoundError


class MessageService:
    async def list_messages(
        self,
        db: AsyncSession,
        *,
        conversation_id: int,
        user_id: int,
        limit: int,
        before_id: int | None,
    ) -> list[Message]:
        # Ownership check first — messages have no user_id of their own.
        conversation = await ConversationRepository(db).get_active_for_user(
            conversation_id, user_id
        )
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return await MessageRepository(db).list_for_conversation(
            conversation_id, limit=limit, before_id=before_id
        )
