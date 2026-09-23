"""Two-step verification settings: verify a contact, then turn the factor on or off.

A destination is stored (``USERS.MOBILE_NUMBER`` or ``USERS.EMAIL``) and the flag
flipped (``USERS.OTP_ENABLED``) only after the code sent to it comes back — a
provider accepting a request is not verification.

A user may verify *both* contacts. Whichever one is verified first becomes the
default method; verifying the second one later leaves that default alone, so
adding an email never quietly moves where login codes go.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.contact import OtpMethod, mask_destination
from app.core.logging import get_logger, structured
from app.core.time import utcnow
from app.db.models.user import User
from app.db.repositories.users import UserRepository
from app.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.services.otp_audit import OtpAudit
from app.services.otp_delivery import Destination, OtpDeliveryService
from app.services.otp_service import OtpService

logger = get_logger("app.services.two_factor")


@dataclass(frozen=True)
class ChallengeIssued:
    """A code was accepted by the provider and is awaiting verification."""

    challenge_id: str
    expires_in_seconds: int
    method: OtpMethod
    destination_hint: str


class TwoFactorService:
    """Enabling/disabling two-step verification for the authenticated user."""

    @staticmethod
    async def _reject_taken_destination(
        db: AsyncSession, *, user_id: int, method: OtpMethod, destination: str
    ) -> None:
        """Refuse a contact another account already verified.

        The user's own contact is fine — that is the "add the other channel" case,
        or simply re-verifying what they already have.
        """
        repo = UserRepository(db)
        if method == "EMAIL":
            holder = await repo.get_by_email(destination)
            taken = "That email address is already linked to another account"
        else:
            holder = await repo.get_by_mobile(int(destination))
            taken = "That mobile number is already linked to another account"
        if holder is not None and holder.id != user_id:
            raise ConflictError(taken)

    async def start_enable(
        self,
        db: AsyncSession,
        *,
        user: User,
        method: OtpMethod,
        destination: str,
        delivery: OtpDeliveryService,
        otp: OtpService,
        audit: OtpAudit,
    ) -> ChallengeIssued:
        """Send a code to ``destination``; nothing is stored until it is verified."""
        if user.otp_enabled == 1 and delivery.destination_for(user, method) == destination:
            raise ConflictError("Two-step verification is already enabled for this destination")
        # A contact belongs to one account (UK_USERS_MOBILE_NUMBER / UK_USERS_EMAIL).
        # Checked here so a code is never sent to an address that cannot be attached
        # — the constraint catches the race, this catches the ordinary case.
        await self._reject_taken_destination(
            db, user_id=user.id, method=method, destination=destination
        )

        challenge_id, code = otp.claim(
            user_id=user.id, purpose="verify_contact", method=method, destination=destination
        )
        try:
            # The destination is the one just supplied (it is being verified, so it
            # is not on the account yet) — resolve() would look for a stored one.
            await delivery.send(
                Destination(method=method, address=destination),
                code=code,
                user=user,
                purpose="verify_contact",
            )
        except ServiceUnavailableError:
            # Nothing was sent, so let the user try again immediately.
            otp.discard(challenge_id, user_id=user.id)
            raise
        otp.commit(challenge_id)
        # The code is on its way, so it belongs in the history.
        await audit.issued(
            challenge=otp.require_live(challenge_id, purpose="verify_contact"),
            method=method,
            ttl_seconds=otp.code_ttl_seconds,
        )
        return ChallengeIssued(
            challenge_id=challenge_id,
            expires_in_seconds=otp.code_ttl_seconds,
            method=method,
            destination_hint=mask_destination(method, destination),
        )

    async def confirm_enable(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        challenge_id: str,
        code: str,
        otp: OtpService,
        audit: OtpAudit,
    ) -> User:
        """Verify the code, then store the destination and enable the second factor."""
        # Consumes the challenge; a failure leaves the account untouched.
        verified = otp.verify(
            challenge_id=challenge_id,
            code=code,
            purpose="verify_contact",
            expected_user_id=user_id,
        )
        if verified.destination is None:
            raise ServiceUnavailableError("Two-step verification is unavailable for this account")
        user = await UserRepository(db).get_by_id(verified.user_id)
        if user is None:
            raise NotFoundError("User not found")

        first_time = user.otp_enabled != 1
        # The destination comes from the verified challenge, never from a second
        # client-supplied value, so it cannot be swapped between the two calls.
        if verified.method == "EMAIL":
            user.email = verified.destination
        else:
            user.mobile_number = int(verified.destination)
        user.otp_enabled = 1
        if first_time:
            # Enabling 2FA must leave the account usable: the default method has to
            # be the contact that was just verified, or the next login would look
            # for a destination that does not exist yet.
            user.preferred_otp_method = verified.method
        user.updated_at = utcnow()
        try:
            await db.commit()
        except IntegrityError as exc:
            # UK_USERS_MOBILE_NUMBER / UK_USERS_EMAIL: another account verified this
            # contact between the check above and here.
            await db.rollback()
            raise ConflictError(
                "That email address is already linked to another account"
                if verified.method == "EMAIL"
                else "That mobile number is already linked to another account"
            ) from exc
        await db.refresh(user)
        structured(
            logger,
            logging.INFO,
            "two-step verification enabled",
            user_id=user.id,
            method=verified.method,
            first_time=first_time,
        )
        # After the commit: the account change is what matters, the history is a record
        # of it.
        await audit.consumed(user_id=user.id, purpose="verify_contact")
        return user

    async def disable(self, db: AsyncSession, *, user_id: int, otp: OtpService) -> User:
        """Turn the second factor off. Verified destinations are kept."""
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
