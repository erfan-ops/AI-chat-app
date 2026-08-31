"""User profile service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.db.models.user import User
from app.db.repositories.ai import ModelRepository
from app.db.repositories.users import UserRepository
from app.exceptions import BadRequestError, NotFoundError


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
        display_name: str | None,
        default_model_id: int | None,
    ) -> User:
        user = await UserRepository(db).get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        if default_model_id is not None:
            if await ModelRepository(db).get_active(default_model_id) is None:
                raise BadRequestError("Unknown or inactive model")
            user.default_model_id = default_model_id
        if display_name is not None:
            user.display_name = display_name
        user.updated_at = utcnow()
        await db.commit()
        await db.refresh(user)
        return user
