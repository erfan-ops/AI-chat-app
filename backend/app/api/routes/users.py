"""Authenticated user profile endpoints: GET /me, PATCH /me."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.users import UserRead, UserUpdate
from app.services.user_service import UserService

router = APIRouter(tags=["users"])

user_service = UserService()


@router.get("/me", response_model=UserRead, summary="Get the authenticated user's profile")
async def get_me(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    return await user_service.get_profile(db, user.id)


@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the authenticated user's profile",
    description=(
        "Update the username, display name and/or the default AI model. "
        "The username must be unique — a duplicate is 409."
    ),
    responses={409: {"description": "Username is already taken"}},
)
async def update_me(
    body: UserUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    return await user_service.update_profile(
        db,
        user.id,
        username=body.username,
        display_name=body.display_name,
        default_model_id=body.default_model_id,
        preferred_otp_method=body.preferred_otp_method,
    )
