"""Character catalog: scoped reads for users, unrestricted CRUD for administrators.

Authorization rules, all enforced here (never in the route):

* **List** — scoped identically for everyone, administrators included: active
  built-in characters plus the caller's own. Another user's private characters and
  any non-active character never appear.
* **Read one** — a normal user may read an active built-in character or one they
  own; an administrator may read any character, any owner and any status.
* **Write** — a normal user may edit or delete only a character they own; an
  administrator may act on any character.
* ``owner_user_id`` and ``status`` are administrator-only. A non-admin who sends one
  gets 403 rather than having it silently dropped.
* Deletion is soft (``STATUS='DELETED'``), matching the conversations convention: the
  row is preserved and its conversations/memories keep their foreign keys.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.db.models.character import Character
from app.db.models.user import User
from app.db.repositories.characters import CharacterRepository
from app.db.repositories.users import UserRepository
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.schemas.ai import CHARACTER_ADMIN_FIELDS

ACTIVE_STATUS = "ACTIVE"
DELETED_STATUS = "DELETED"
# No check constraint exists; the API defines the value set (see docs/database.md).
VALID_STATUSES = frozenset({ACTIVE_STATUS, DELETED_STATUS})

# Columns a client may change; ID and CREATED_AT are immutable, UPDATED_AT is ours.
USER_EDITABLE_FIELDS = frozenset({"name", "description", "avatar_url", "system_prompt"})
ADMIN_EDITABLE_FIELDS = USER_EDITABLE_FIELDS | CHARACTER_ADMIN_FIELDS


class CharacterService:
    async def list_for_user(self, db: AsyncSession, actor: User) -> list[Character]:
        repo = CharacterRepository(db)
        return await repo.list_visible(actor.id)

    async def get_for_user(self, db: AsyncSession, character_id: int, actor: User) -> Character:
        character = await self._load_readable(db, character_id, actor)
        if character is None:
            raise NotFoundError("Character not found")
        return character

    async def create(
        self,
        db: AsyncSession,
        *,
        actor: User,
        name: str,
        description: str | None,
        avatar_url: str | None,
        system_prompt: str | None,
        owner_user_id: int | None = None,
        status: str | None = None,
        admin_fields: frozenset[str] = frozenset(),
    ) -> Character:
        """Create a character owned by ``actor`` unless an admin says otherwise."""
        self._reject_admin_fields(actor, admin_fields)
        # Only an admin can reach this branch, so owner/status are safe to honour.
        owner = owner_user_id if "owner_user_id" in admin_fields else actor.id
        await self._validate_owner(db, owner)
        if "status" in admin_fields and status:
            self._validate_status(status)
        now = utcnow()
        character = Character(
            name=name,
            description=description,
            avatar_url=avatar_url,
            system_prompt=system_prompt,
            status=status if "status" in admin_fields and status else ACTIVE_STATUS,
            owner_user_id=owner,
            created_at=now,
            updated_at=now,
        )
        await CharacterRepository(db).add(character)
        await db.commit()
        await db.refresh(character)  # picks up the identity-generated ID
        return character

    async def update(
        self,
        db: AsyncSession,
        character_id: int,
        *,
        actor: User,
        changes: dict[str, Any],
        admin_fields: frozenset[str] = frozenset(),
    ) -> Character:
        self._reject_admin_fields(actor, admin_fields)
        character = await self._load_writable(db, character_id, actor)
        if "owner_user_id" in admin_fields:
            await self._validate_owner(db, changes.get("owner_user_id"))
        if changes.get("status") is not None:
            self._validate_status(changes["status"])
        allowed = ADMIN_EDITABLE_FIELDS if actor.is_admin else USER_EDITABLE_FIELDS
        for field, value in changes.items():
            if field in allowed:  # whitelist — ID/CREATED_AT can never be assigned
                setattr(character, field, value)
        character.updated_at = utcnow()
        await db.commit()
        await db.refresh(character)
        return character

    async def soft_delete(self, db: AsyncSession, character_id: int, actor: User) -> None:
        """Mark the character DELETED; the row and its conversations are preserved."""
        character = await self._load_writable(db, character_id, actor)
        character.status = DELETED_STATUS
        character.updated_at = utcnow()
        await db.commit()

    def _reject_admin_fields(self, actor: User, admin_fields: frozenset[str]) -> None:
        if admin_fields and not actor.is_admin:
            listed = ", ".join(sorted(admin_fields))
            raise ForbiddenError(f"Administrator privileges required to set: {listed}")

    def _validate_status(self, status: str) -> None:
        if status not in VALID_STATUSES:
            raise BadRequestError(f"Status must be one of: {', '.join(sorted(VALID_STATUSES))}")

    async def _validate_owner(self, db: AsyncSession, owner_user_id: int | None) -> None:
        """A reassigned owner must exist — the column is a foreign key to USERS."""
        if owner_user_id is None:
            return
        if await UserRepository(db).get_by_id(owner_user_id) is None:
            raise BadRequestError("Unknown owner user")

    async def _load_readable(
        self, db: AsyncSession, character_id: int, actor: User
    ) -> Character | None:
        repo = CharacterRepository(db)
        if actor.is_admin:
            return await repo.get_by_id(character_id)
        return await repo.get_visible(character_id, actor.id)

    async def _load_writable(self, db: AsyncSession, character_id: int, actor: User) -> Character:
        """A character the actor may modify.

        404 when they cannot even see it (a foreign private character is
        indistinguishable from a nonexistent one); 403 when they can see it but do
        not own it (a built-in character).
        """
        character = await self._load_readable(db, character_id, actor)
        if character is None:
            raise NotFoundError("Character not found")
        if not actor.is_admin and character.owner_user_id != actor.id:
            raise ForbiddenError("You can only modify your own characters")
        return character
