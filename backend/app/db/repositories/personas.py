"""Data access for USER_PERSONAS.

Personas are private. Ownership filtering happens here (``user_id`` in every query)
so a foreign persona id is indistinguishable from a nonexistent one.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversation import Conversation
from app.db.models.persona import UserPersona


class PersonaRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_user(self, user_id: int) -> list[UserPersona]:
        stmt = select(UserPersona).where(UserPersona.user_id == user_id).order_by(UserPersona.id)
        return list((await self._db.scalars(stmt)).all())

    async def get_for_user(self, persona_id: int, user_id: int) -> UserPersona | None:
        stmt = select(UserPersona).where(
            UserPersona.id == persona_id, UserPersona.user_id == user_id
        )
        return (await self._db.scalars(stmt)).first()

    async def add(self, persona: UserPersona) -> UserPersona:
        """Stage an insert; the caller commits."""
        self._db.add(persona)
        await self._db.flush()
        return persona

    async def detach_from_conversations(self, persona_id: int) -> None:
        """Clear USER_PERSONA_ID wherever it points at this persona.

        Run before deleting a persona: the conversations survive and fall back to
        the plain character prompt, exactly like a conversation created without one.
        """
        await self._db.execute(
            update(Conversation)
            .where(Conversation.user_persona_id == persona_id)
            .values(user_persona_id=None)
        )

    async def delete(self, persona: UserPersona) -> None:
        await self._db.delete(persona)
