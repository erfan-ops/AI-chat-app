"""Two-step verification settings for the authenticated user.

Enabling is a two-call flow: ``/me/otp/enable`` sends a code, ``/me/otp/verify``
proves ownership and only then stores the number and flips the flag. Disabling is a
single authenticated call — the user is already signed in.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_otp_service, get_sms_service
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.otp import OtpChallengeRead, OtpEnableRequest, OtpVerifyRequest
from app.schemas.users import UserRead
from app.services.otp_service import OtpService
from app.services.sms_service import SmsService
from app.services.two_factor_service import TwoFactorService

router = APIRouter(tags=["users"])

two_factor_service = TwoFactorService()


@router.post(
    "/me/otp/enable",
    response_model=OtpChallengeRead,
    summary="Start enabling two-step verification",
    description=(
        "Sends a one-time code to the supplied mobile number. Nothing is stored and "
        "the second factor stays off until `POST /me/otp/verify` confirms the code."
    ),
)
async def start_otp_enable(
    body: OtpEnableRequest,
    user: Annotated[User, Depends(get_current_user)],
    sms: Annotated[SmsService, Depends(get_sms_service)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
) -> OtpChallengeRead:
    issued = await two_factor_service.start_enable(
        user=user, mobile=body.mobile_number, sms=sms, otp=otp
    )
    return OtpChallengeRead(
        challenge_id=issued.challenge_id,
        code_expires_in_seconds=issued.expires_in_seconds,
        mobile_hint=issued.mobile_hint,
    )


@router.post(
    "/me/otp/verify",
    response_model=UserRead,
    summary="Confirm the code and enable two-step verification",
    description=(
        "Verifies the code sent by `/me/otp/enable`, stores the verified mobile "
        "number and turns two-step verification on. An invalid or expired code is "
        "rejected with 400 and leaves the account unchanged."
    ),
)
async def verify_otp_enable(
    body: OtpVerifyRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
) -> User:
    return await two_factor_service.confirm_enable(
        db,
        user_id=user.id,
        challenge_id=body.challenge_id,
        code=body.code,
        otp=otp,
    )


@router.post(
    "/me/otp/disable",
    response_model=UserRead,
    summary="Disable two-step verification",
    description=(
        "Turns two-step verification off for the authenticated user and discards any "
        "code still in flight. The verified mobile number is kept."
    ),
)
async def disable_otp(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
) -> User:
    return await two_factor_service.disable(db, user_id=user.id, otp=otp)
