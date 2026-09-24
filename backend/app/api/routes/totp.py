"""Authenticator-app (TOTP) enrolment for the authenticated user.

One endpoint begins it: a fresh secret is generated and stored, and the otpauth://
URI comes back for the client to render as a QR code. Confirmation reuses
``POST /me/otp/verify`` — the challenge it was given says the code must be checked
against the enrolled secret and the corrected clock, so enabling an authenticator and
verifying an email address are the same call with the same attempt budget.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_otp_service, get_totp_service
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.otp import TotpEnrollRead
from app.services.otp_service import OtpService
from app.services.totp_service import TotpService
from app.services.two_factor_service import TwoFactorService

router = APIRouter(tags=["users"])

two_factor_service = TwoFactorService()


@router.post(
    "/me/totp/enable",
    response_model=TotpEnrollRead,
    summary="Start enrolling an authenticator app",
    description=(
        "Generates a TOTP secret, stores it, and returns the `otpauth://` URI (for a "
        "QR code) plus the Base32 secret for entering by hand.\n\n"
        "**Two-step verification is not enabled by this call.** Only a correct code "
        "from the authenticator turns it on — see `POST /me/otp/verify` with the "
        "returned `challenge_id`. Enrolling again replaces the previous secret."
    ),
)
async def start_totp_enable(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
    totp: Annotated[TotpService, Depends(get_totp_service)],
) -> TotpEnrollRead:
    enrollment = await two_factor_service.start_totp_enroll(
        db,
        user=user,
        totp=totp,
        otp=otp,
        ttl_seconds=settings.totp_enroll_ttl_seconds,
    )
    return TotpEnrollRead(
        challenge_id=enrollment.challenge_id,
        secret=enrollment.secret,
        otpauth_uri=enrollment.otpauth_uri,
        code_expires_in_seconds=enrollment.expires_in_seconds,
    )
