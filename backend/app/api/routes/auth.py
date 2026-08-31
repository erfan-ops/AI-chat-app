"""Public authentication endpoints: POST /auth/register, POST /auth/login."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_auth_service
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, RegisterRequest
from app.schemas.users import UserRead
from app.services.auth_service import LoginResult

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
    description=(
        "Creates a user account. The password is hashed with Argon2id and never "
        "stored in plaintext. Usernames are unique."
    ),
)
async def register(
    body: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    return await get_auth_service(settings).register(
        db, username=body.username, password=body.password, display_name=body.display_name
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Log in and obtain an access token",
    description=(
        "Exchanges username/password for a signed JWT access token. Send it as "
        "`Authorization: Bearer <token>` on protected endpoints."
    ),
)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    result: LoginResult = await get_auth_service(settings).login(
        db, username=body.username, password=body.password
    )
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in_minutes,
        user=UserRead.model_validate(result.user),
    )
