"""User profile service."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.db.models.user import User
from app.db.repositories.ai import ModelRepository
from app.db.repositories.users import UserRepository
from app.exceptions import BadRequestError, ConflictError, NotFoundError

USERNAME_TAKEN = "Username is already taken"


class UserService:
    async def get_profile(self, db: AsyncSession, user_id: int) -> User:
        user = await UserRepository(db).get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def update_profile(
        self,
        db: AsyncSession,
        user_id: int,
        *,
        username: str | None,
        display_name: str | None,
        default_model_id: int | None,
    ) -> User:
        repo = UserRepository(db)
        user = await repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        if username is not None and username != user.username:
            existing = await repo.get_by_username(username)
            if existing is not None and existing.id != user.id:
                raise ConflictError(USERNAME_TAKEN)
            user.username = username
        if default_model_id is not None:
            if await ModelRepository(db).get_active(default_model_id) is None:
                raise BadRequestError("Unknown or inactive model")
            user.default_model_id = default_model_id
        if display_name is not None:
            user.display_name = display_name
        user.updated_at = utcnow()
        try:
            await db.commit()
        except IntegrityError as exc:
            # UK_USERS_USERNAME is the real guarantee: the pre-check above can
            # lose a race with a concurrent registration of the same name.
            await db.rollback()
            raise ConflictError(USERNAME_TAKEN) from exc
        await db.refresh(user)
        return user
