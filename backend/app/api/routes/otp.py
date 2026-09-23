"""Two-step verification settings for the authenticated user.

Enabling is a two-call flow: ``/me/otp/enable`` sends a code, ``/me/otp/verify``
proves ownership and only then stores the destination and flips the flag. Either
contact can be verified this way — a mobile number (SMS) or an email address
(email) — and a user may verify both. Disabling is a single authenticated call: the
user is already signed in.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_user,
    get_otp_audit,
    get_otp_delivery_service,
    get_otp_service,
)
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.otp import OtpChallengeRead, OtpEnableRequest, OtpVerifyRequest
from app.schemas.users import UserRead
from app.services.otp_audit import OtpAudit
from app.services.otp_delivery import OtpDeliveryService
from app.services.otp_service import OtpService
from app.services.two_factor_service import TwoFactorService

router = APIRouter(tags=["users"])

two_factor_service = TwoFactorService()


@router.post(
    "/me/otp/enable",
    response_model=OtpChallengeRead,
    summary="Start enabling two-step verification",
    description=(
        "Sends a one-time code to the supplied contact, by SMS (`mobile_number`) or "
        "email (`email`) depending on `method`. Nothing is stored and the second "
        "factor stays off until `POST /me/otp/verify` confirms the code."
    ),
)
async def start_otp_enable(
    body: OtpEnableRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    delivery: Annotated[OtpDeliveryService, Depends(get_otp_delivery_service)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
    audit: Annotated[OtpAudit, Depends(get_otp_audit)],
) -> OtpChallengeRead:
    destination = body.mobile_number if body.method == "SMS" else body.email
    assert destination is not None  # guaranteed by OtpEnableRequest
    issued = await two_factor_service.start_enable(
        db,
        user=user,
        method=body.method,
        destination=destination,
        delivery=delivery,
        otp=otp,
        audit=audit,
    )
    return OtpChallengeRead(
        challenge_id=issued.challenge_id,
        code_expires_in_seconds=issued.expires_in_seconds,
        method=issued.method,
        destination_hint=issued.destination_hint,
    )


@router.post(
    "/me/otp/verify",
    response_model=UserRead,
    summary="Confirm the code and enable two-step verification",
    description=(
        "Verifies the code sent by `/me/otp/enable`, stores the verified contact and "
        "turns two-step verification on (a first-time enable also makes that contact "
        "the default delivery method). An invalid or expired code is rejected with "
        "400 and leaves the account unchanged."
    ),
)
async def verify_otp_enable(
    body: OtpVerifyRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
    audit: Annotated[OtpAudit, Depends(get_otp_audit)],
) -> User:
    return await two_factor_service.confirm_enable(
        db,
        user_id=user.id,
        challenge_id=body.challenge_id,
        code=body.code,
        otp=otp,
        audit=audit,
    )


@router.post(
    "/me/otp/disable",
    response_model=UserRead,
    summary="Disable two-step verification",
    description=(
        "Turns two-step verification off for the authenticated user and discards any "
        "code still in flight. The verified contacts are kept."
    ),
)
async def disable_otp(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
) -> User:
    return await two_factor_service.disable(db, user_id=user.id, otp=otp)
