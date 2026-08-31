"""User personas: private, user-owned CRUD.

Every operation is scoped to the authenticated user — the owner comes from the access
token and a foreign persona id yields 404, indistinguishable from a nonexistent one.
Administrators get no special access here: a persona is personal data, not catalog
configuration like characters and models.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.persona import UserPersona
from app.db.repositories.personas import PersonaRepository
from app.exceptions import NotFoundError

EDITABLE_FIELDS = frozenset({"name", "gender", "description", "age"})


class PersonaService:
    async def list_for_user(self, db: AsyncSession, user_id: int) -> list[UserPersona]:
        return await PersonaRepository(db).list_for_user(user_id)

    async def get_for_user(self, db: AsyncSession, persona_id: int, user_id: int) -> UserPersona:
        persona = await PersonaRepository(db).get_for_user(persona_id, user_id)
        if persona is None:
            raise NotFoundError("Persona not found")
        return persona

    async def create(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        name: str,
        gender: str | None,
        description: str | None,
        age: int | None,
    ) -> UserPersona:
        """Create a persona owned by ``user_id``; only the name is required."""
        persona = UserPersona(
            user_id=user_id,
            name=name,
            gender=gender,
            description=description,
            age=age,
        )
        await PersonaRepository(db).add(persona)
        await db.commit()
        await db.refresh(persona)  # picks up the identity-generated ID
        return persona

    async def update(
        self, db: AsyncSession, persona_id: int, user_id: int, *, changes: dict[str, Any]
    ) -> UserPersona:
        persona = await self.get_for_user(db, persona_id, user_id)
        for field, value in changes.items():
            if field in EDITABLE_FIELDS:  # whitelist — ID/USER_ID are never assignable
                setattr(persona, field, value)
        await db.commit()
        await db.refresh(persona)
        return persona

    async def delete(self, db: AsyncSession, persona_id: int, user_id: int) -> None:
        """Delete the persona; conversations using it fall back to no persona."""
        persona = await self.get_for_user(db, persona_id, user_id)
        repo = PersonaRepository(db)
        await repo.detach_from_conversations(persona.id)
        await repo.delete(persona)
        await db.commit()
