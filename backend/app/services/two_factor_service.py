"""Two-step verification settings: verify a mobile number, then turn it on or off.

The number is stored (``USERS.MOBILE_NUMBER``) and the flag flipped
(``USERS.OTP_ENABLED``) only after the code sent to that number comes back — SMS.ir
accepting a request is not verification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, structured
from app.core.time import utcnow
from app.db.models.user import User
from app.db.repositories.users import UserRepository
from app.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.services.otp_service import OtpService
from app.services.sms_service import SmsService

logger = get_logger("app.services.two_factor")


@dataclass(frozen=True)
class ChallengeIssued:
    """A code was accepted by the provider and is awaiting verification."""

    challenge_id: str
    expires_in_seconds: int
    mobile_hint: str


def mask_mobile(mobile: str) -> str:
    """``9123456789`` → ``+98 912 *** 6789`` — enough to recognise, not to harvest."""
    return f"+98 {mobile[:3]} *** {mobile[6:]}"


class TwoFactorService:
    """Enabling/disabling two-step verification for the authenticated user."""

    async def start_enable(
        self, *, user: User, mobile: str, sms: SmsService, otp: OtpService
    ) -> ChallengeIssued:
        """Send a code to ``mobile``; nothing is stored until it is verified."""
        existing = f"{user.mobile_number:010d}" if user.mobile_number else None
        if user.otp_enabled == 1 and existing == mobile:
            raise ConflictError("Two-step verification is already enabled for this number")

        challenge_id, code = otp.issue(user_id=user.id, purpose="enable", mobile=mobile)
        try:
            await sms.send_verify_code(
                mobile=mobile,
                code=code,
                display_name=user.display_name,
                username=user.username,
                user_id=user.id,
            )
        except ServiceUnavailableError:
            # Nothing was sent, so let the user try again immediately.
            otp.discard(challenge_id, user_id=user.id)
            raise
        return ChallengeIssued(
            challenge_id=challenge_id,
            expires_in_seconds=otp.code_ttl_seconds,
            mobile_hint=mask_mobile(mobile),
        )

    async def confirm_enable(
        self, db: AsyncSession, *, user_id: int, challenge_id: str, code: str, otp: OtpService
    ) -> User:
        """Verify the code, then store the number and enable the second factor."""
        # Consumes the challenge; a failure leaves the account untouched.
        verified = otp.verify(
            challenge_id=challenge_id,
            code=code,
            purpose="enable",
            expected_user_id=user_id,
        )
        if verified.mobile is None:
            raise ServiceUnavailableError("Two-step verification is unavailable for this account")
        user = await UserRepository(db).get_by_id(verified.user_id)
        if user is None:
            raise NotFoundError("User not found")

        # The number comes from the verified challenge, never from a second
        # client-supplied value, so it cannot be swapped between the two calls.
        user.mobile_number = int(verified.mobile)
        user.otp_enabled = 1
        user.updated_at = utcnow()
        await db.commit()
        await db.refresh(user)
        structured(logger, logging.INFO, "two-step verification enabled", user_id=user.id)
        return user

    async def disable(self, db: AsyncSession, *, user_id: int, otp: OtpService) -> User:
        """Turn the second factor off. The verified number is kept."""
        user = await UserRepository(db).get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        user.otp_enabled = 0
        user.updated_at = utcnow()
        await db.commit()
        await db.refresh(user)
        # Any code still in flight must not be able to complete a login.
        otp.invalidate_user(user_id)
        structured(logger, logging.INFO, "two-step verification disabled", user_id=user.id)
        return user
