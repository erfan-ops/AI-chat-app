"""Conversation lifecycle: create/list/get/rename/soft-delete with ownership checks."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.db.models.ai import AIModel
from app.db.models.conversation import Conversation
from app.db.repositories.ai import ModelRepository
from app.db.repositories.characters import CharacterRepository
from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.personas import PersonaRepository
from app.db.repositories.users import UserRepository
from app.exceptions import BadRequestError, NotFoundError, ServiceUnavailableError

ACTIVE_STATUS = "ACTIVE"
DELETED_STATUS = "DELETED"


class ConversationService:
    async def list_for_user(
        self, db: AsyncSession, user_id: int, *, limit: int, offset: int
    ) -> list[Conversation]:
        return await ConversationRepository(db).list_for_user(user_id, limit=limit, offset=offset)

    async def create(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        character_id: int,
        model_id: int | None,
        title: str | None,
        user_persona_id: int | None = None,
    ) -> Conversation:
        character = await CharacterRepository(db).get_visible(character_id, user_id)
        if character is None:
            raise NotFoundError("Character not found")
        model = await self._resolve_model(db, user_id=user_id, model_id=model_id)
        await self._verify_persona(db, user_id=user_id, user_persona_id=user_persona_id)
        now = utcnow()
        conversation = Conversation(
            user_id=user_id,
            character_id=character_id,
            model_id=model.id,
            user_persona_id=user_persona_id,
            title=title or f"Chat with {character.name}",
            status=ACTIVE_STATUS,
            created_at=now,
            updated_at=now,
        )
        await ConversationRepository(db).add(conversation)
        await db.commit()
        await db.refresh(conversation, attribute_names=["character", "model", "user_persona"])
        return conversation

    async def _verify_persona(
        self, db: AsyncSession, *, user_id: int, user_persona_id: int | None
    ) -> None:
        """A supplied persona must exist *and* belong to the caller.

        Never trust the id from the request body: a persona owned by someone else is
        rejected with the same 404 as one that does not exist.
        """
        if user_persona_id is None:
            return
        if await PersonaRepository(db).get_for_user(user_persona_id, user_id) is None:
            raise NotFoundError("Persona not found")

    async def get_for_user(
        self, db: AsyncSession, conversation_id: int, user_id: int
    ) -> Conversation:
        conversation = await ConversationRepository(db).get_active_for_user_detailed(
            conversation_id, user_id
        )
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation

    async def update_title(
        self, db: AsyncSession, conversation_id: int, user_id: int, *, title: str
    ) -> Conversation:
        conversation = await ConversationRepository(db).get_active_for_user(
            conversation_id, user_id
        )
        if conversation is None:
            raise NotFoundError("Conversation not found")
        conversation.title = title
        conversation.updated_at = utcnow()
        await db.commit()
        await db.refresh(conversation, attribute_names=["character", "model", "user_persona"])
        return conversation

    async def soft_delete(self, db: AsyncSession, conversation_id: int, user_id: int) -> None:
        """Soft delete: the row and its messages are preserved (STATUS='DELETED')."""
        conversation = await ConversationRepository(db).get_active_for_user(
            conversation_id, user_id
        )
        if conversation is None:
            raise NotFoundError("Conversation not found")
        conversation.status = DELETED_STATUS
        conversation.updated_at = utcnow()
        await db.commit()

    async def _resolve_model(
        self, db: AsyncSession, *, user_id: int, model_id: int | None
    ) -> AIModel:
        """Explicit model → user's default model → first active model."""
        models = ModelRepository(db)
        if model_id is not None:
            model = await models.get_active(model_id)
            if model is None:
                raise BadRequestError("Unknown or inactive model")
            return model
        user = await UserRepository(db).get_by_id(user_id)
        if user is not None and user.default_model_id is not None:
            model = await models.get_active(user.default_model_id)
            if model is not None:
                return model
        fallback = await models.first_active()
        if fallback is None:
            raise ServiceUnavailableError("No active AI model is configured")
        return fallback
