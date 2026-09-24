"""Authenticated user profile endpoints: GET /me, PATCH /me, POST /me/password."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_auth_service, get_current_user
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.auth import PasswordChangeRequest
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


@router.post(
    "/me/password",
    response_model=UserRead,
    summary="Change the authenticated user's password",
    description=(
        "Replaces the password after checking the current one. A wrong current "
        "password is rejected with 400 — never 401, which would sign the caller out "
        "of the session they are asking from — and repeated failures are throttled "
        "by the same counter as login.\n\n"
        "Existing access tokens keep working: this codebase has no token revocation, "
        "so the new password takes effect at the next sign-in."
    ),
    responses={400: {"description": "The current password is incorrect"}},
)
async def change_password(
    body: PasswordChangeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    return await get_auth_service(settings).change_password(
        db,
        user_id=user.id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
