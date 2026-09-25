"""User profile service."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.contact import OtpMethod
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
        preferred_otp_method: OtpMethod | None = None,
        avatar_url: str | None = None,
        provided: frozenset[str] = frozenset(),
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
        if preferred_otp_method is not None:
            # A default without a verified contact would make the next login fail
            # closed, so the preference may only ever name a channel that exists.
            verified = user.email if preferred_otp_method == "EMAIL" else user.mobile_number
            if not verified:
                raise BadRequestError(
                    "Verify an email address first"
                    if preferred_otp_method == "EMAIL"
                    else "Verify a mobile number first"
                )
            user.preferred_otp_method = preferred_otp_method
        if default_model_id is not None:
            if await ModelRepository(db).get_active(default_model_id) is None:
                raise BadRequestError("Unknown or inactive model")
            user.default_model_id = default_model_id
        if display_name is not None:
            user.display_name = display_name
        # Keyed on what the client sent rather than on the value: `null` here means
        # "remove the picture", so an absent field is the only way to leave it alone.
        if "avatar_url" in provided:
            user.avatar_url = avatar_url
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
