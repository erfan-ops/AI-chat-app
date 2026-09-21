"""Public authentication endpoints: register, and the two-step login flow."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_auth_service, get_otp_service, get_sms_service
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.auth import (
    LoginOtpRequest,
    LoginRequest,
    LoginResponse,
    OtpRequiredResponse,
    RegisterRequest,
)
from app.schemas.users import UserRead
from app.services.auth_service import LoginResult, OtpChallengeResult
from app.services.otp_service import OtpService
from app.services.sms_service import SmsService

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
    response_model=LoginResponse | OtpRequiredResponse,
    summary="Log in and obtain an access token",
    description=(
        "Exchanges username/password for a signed JWT access token. Send it as "
        "`Authorization: Bearer <token>` on protected endpoints.\n\n"
        "When the account has two-step verification enabled, no token is issued: "
        "the response instead carries `otp_required` plus a `challenge_id`, and the "
        "login is completed with `POST /auth/login/otp`."
    ),
)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    sms: Annotated[SmsService, Depends(get_sms_service)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
) -> LoginResponse | OtpRequiredResponse:
    result = await get_auth_service(settings).login(
        db, username=body.username, password=body.password, sms=sms, otp=otp
    )
    if isinstance(result, OtpChallengeResult):
        return OtpRequiredResponse(
            challenge_id=result.challenge_id,
            code_expires_in_seconds=result.expires_in_seconds,
        )
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in_minutes,
        user=UserRead.model_validate(result.user),
    )


@router.post(
    "/login/otp",
    response_model=LoginResponse,
    summary="Complete a two-step login",
    description=(
        "Second step for accounts with two-step verification: exchanges the "
        "`challenge_id` from `POST /auth/login` plus the code received by SMS for "
        "the same access token a normal login would have returned. An incorrect or "
        "expired code is rejected with 400 — never 401, which would sign the caller "
        "out of an unrelated session."
    ),
)
async def login_with_otp(
    body: LoginOtpRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
) -> LoginResponse:
    result: LoginResult = await get_auth_service(settings).complete_otp_login(
        db, challenge_id=body.challenge_id, code=body.code, otp=otp
    )
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in_minutes,
        user=UserRead.model_validate(result.user),
    )
