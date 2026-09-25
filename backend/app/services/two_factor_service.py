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

from app.core.contact import SentOtpMethod, mask_destination
from app.core.logging import get_logger, structured
from app.core.time import utcnow
from app.db.models.user import User
from app.db.repositories.users import UserRepository
from app.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.services.otp_audit import OtpAudit
from app.services.otp_delivery import Destination, OtpDeliveryService
from app.services.otp_service import OtpService
from app.services.totp_service import TotpService, totp_validator

logger = get_logger("app.services.two_factor")


@dataclass(frozen=True)
class ChallengeIssued:
    """A code was accepted by the provider and is awaiting verification."""

    challenge_id: str
    expires_in_seconds: int
    method: SentOtpMethod
    destination_hint: str


@dataclass(frozen=True)
class TotpEnrollment:
    """An authenticator was provisioned; a code from it is still needed."""

    challenge_id: str
    secret: str
    otpauth_uri: str
    expires_in_seconds: int


class TwoFactorService:
    """Enabling/disabling two-step verification for the authenticated user."""

    @staticmethod
    async def _reject_taken_destination(
        db: AsyncSession, *, user_id: int, method: SentOtpMethod, destination: str
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
        method: SentOtpMethod,
        destination: str,
        delivery: OtpDeliveryService,
        otp: OtpService,
        audit: OtpAudit,
        client_ip: str | None = None,
    ) -> ChallengeIssued:
        """Send a code to ``destination``; nothing is stored until it is verified.

        ``client_ip`` feeds the per-address send window; ``destination`` feeds the
        per-destination one (see ``OtpService._reserve``).
        """
        if user.otp_enabled == 1 and delivery.destination_for(user, method) == destination:
            raise ConflictError("Two-step verification is already enabled for this destination")
        # A contact belongs to one account (UK_USERS_MOBILE_NUMBER / UK_USERS_EMAIL).
        # Checked here so a code is never sent to an address that cannot be attached
        # — the constraint catches the race, this catches the ordinary case.
        await self._reject_taken_destination(
            db, user_id=user.id, method=method, destination=destination
        )

        # Before the claim, so a provider that cannot send does not spend the send
        # budget — and answers "not configured" rather than a rate limit.
        delivery.require_configured(method)
        challenge_id, code = otp.claim(
            user_id=user.id,
            purpose="verify_contact",
            method=method,
            destination=destination,
            client_ip=client_ip,
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

    async def start_totp_enroll(
        self,
        db: AsyncSession,
        *,
        user: User,
        totp: TotpService,
        otp: OtpService,
        ttl_seconds: int,
    ) -> TotpEnrollment:
        """Generate and store an authenticator secret, without enabling anything.

        ``OTP_ENABLED`` stays untouched: holding a secret proves nothing until a code
        from it comes back, which is what ``confirm_enable`` checks. Enrolling again
        overwrites the previous secret, so a re-enrolment invalidates the old app
        entry rather than leaving two working ones.

        The secret and the URI are returned once, to the authenticated owner, and are
        never logged.
        """
        # Loaded through this session on purpose: ``get_current_user`` resolves the
        # authenticated user in its own short-lived session, so the instance the route
        # holds is detached and assigning a secret to it would write nothing.
        owner = await UserRepository(db).get_by_id(user.id)
        if owner is None:
            raise NotFoundError("User not found")
        secret = totp.generate_secret()
        owner.totp_secret = secret
        owner.updated_at = utcnow()
        await db.commit()
        # Nothing is sent, so the challenge is installed immediately — there is no
        # provider that could refuse it. Enrolment is deliberately unthrottled: it is
        # not a login attempt, and throttling it would delay the login that follows.
        challenge_id = otp.claim_totp(
            user_id=owner.id, purpose="verify_contact", ttl_seconds=ttl_seconds, throttled=False
        )
        otp.commit(challenge_id)
        structured(
            logger,
            logging.INFO,
            "authenticator enrolment started",
            user_id=owner.id,
            otp_enabled=owner.otp_enabled,
        )
        return TotpEnrollment(
            challenge_id=challenge_id,
            secret=secret,
            otpauth_uri=totp.provisioning_uri(secret=secret, username=owner.username),
            expires_in_seconds=ttl_seconds,
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
        totp: TotpService | None = None,
    ) -> User:
        """Verify the code, then store the destination and enable the second factor.

        The challenge decides how the code is checked: an authenticator code against
        the enrolled secret and the corrected clock, a sent code against its hash.
        """
        # Consumes the challenge; a failure leaves the account untouched.
        pending = otp.require_live(challenge_id, purpose="verify_contact")
        validator = None
        if pending.method == "TOTP":
            owner = await UserRepository(db).get_by_id(user_id)
            if owner is None:
                raise NotFoundError("User not found")
            if totp is None:
                raise ServiceUnavailableError(
                    "Two-step verification is unavailable for this account"
                )
            validator = totp_validator(owner, totp)
        verified = otp.verify(
            challenge_id=challenge_id,
            code=code,
            purpose="verify_contact",
            expected_user_id=user_id,
            validator=validator,
        )
        if verified.method != "TOTP" and verified.destination is None:
            raise ServiceUnavailableError("Two-step verification is unavailable for this account")
        user = await UserRepository(db).get_by_id(verified.user_id)
        if user is None:
            raise NotFoundError("User not found")

        first_time = user.otp_enabled != 1
        # The destination comes from the verified challenge, never from a second
        # client-supplied value, so it cannot be swapped between the two calls. A TOTP
        # challenge has no destination: the secret is already stored.
        if verified.method == "EMAIL" and verified.destination is not None:
            user.email = verified.destination
        elif verified.method == "SMS" and verified.destination is not None:
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
