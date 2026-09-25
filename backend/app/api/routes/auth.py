"""Public authentication endpoints: register, and the two-step login flow."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    client_address,
    get_auth_service,
    get_otp_audit,
    get_otp_delivery_service,
    get_otp_service,
    get_totp_service,
)
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.auth import (
    LoginOtpMethodRequest,
    LoginOtpRequest,
    LoginRequest,
    LoginResponse,
    OtpRequiredResponse,
    RegisterRequest,
)
from app.schemas.users import UserRead
from app.services.auth_service import LoginResult, OtpChallengeResult
from app.services.otp_audit import OtpAudit
from app.services.otp_delivery import OtpDeliveryService
from app.services.otp_service import OtpService
from app.services.totp_service import TotpService

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
        "login is completed with `POST /auth/login/otp`. The code goes to the "
        "account's default delivery method (`SMS` or `EMAIL`); "
        "`POST /auth/login/otp/method` can send it to the other one instead."
    ),
)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    delivery: Annotated[OtpDeliveryService, Depends(get_otp_delivery_service)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
    audit: Annotated[OtpAudit, Depends(get_otp_audit)],
    client_ip: Annotated[str | None, Depends(client_address)],
) -> LoginResponse | OtpRequiredResponse:
    result = await get_auth_service(settings).login(
        db,
        username=body.username,
        password=body.password,
        delivery=delivery,
        otp=otp,
        audit=audit,
        client_ip=client_ip,
    )
    if isinstance(result, OtpChallengeResult):
        return OtpRequiredResponse(
            challenge_id=result.challenge_id,
            code_expires_in_seconds=result.expires_in_seconds,
            delivery_method=result.method,
            alternative_method=result.alternative_method,
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
        "`challenge_id` from `POST /auth/login` plus the code that was sent for "
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
    audit: Annotated[OtpAudit, Depends(get_otp_audit)],
    totp: Annotated[TotpService, Depends(get_totp_service)],
) -> LoginResponse:
    result: LoginResult = await get_auth_service(settings).complete_otp_login(
        db, challenge_id=body.challenge_id, code=body.code, otp=otp, totp=totp, audit=audit
    )
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in_minutes,
        user=UserRead.model_validate(result.user),
    )


@router.post(
    "/login/otp/method",
    response_model=OtpRequiredResponse,
    summary="Send this login's code through the other delivery method",
    description=(
        "Sends a fresh code for the same login through `method` instead of the "
        "channel the first code used, and returns the new `challenge_id`. This is a "
        "choice for this login only: the account's saved default is untouched."
        "\n\n`method` may be `TOTP`, in which case nothing is sent: the challenge is "
        "for a code from the account's authenticator app, and no SMS or email goes out."
        "\n\nThe destination is always the one stored on the account — the request "
        "names a channel, never an address. Rejections are 400-level (never 401), "
        "and 503 when the account has no verified contact for that channel."
    ),
    responses={
        400: {"description": "Unknown or expired challenge"},
        503: {"description": "That channel is unavailable for this account"},
    },
)
async def login_switch_otp_method(
    body: LoginOtpMethodRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    delivery: Annotated[OtpDeliveryService, Depends(get_otp_delivery_service)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
    audit: Annotated[OtpAudit, Depends(get_otp_audit)],
    client_ip: Annotated[str | None, Depends(client_address)],
) -> OtpRequiredResponse:
    result = await get_auth_service(settings).switch_otp_method(
        db,
        challenge_id=body.challenge_id,
        method=body.method,
        delivery=delivery,
        otp=otp,
        audit=audit,
        client_ip=client_ip,
    )
    return OtpRequiredResponse(
        challenge_id=result.challenge_id,
        code_expires_in_seconds=result.expires_in_seconds,
        delivery_method=result.method,
        alternative_method=result.alternative_method,
    )
