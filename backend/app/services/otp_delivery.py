"""Picks the provider a verification code travels through.

The OTP logic (generation, expiry, attempts, rate limits) knows nothing about SMS
or email; the two provider services know nothing about challenges. This is the
single seam between them: it answers "where can this user be reached, and is that
provider usable?" and then hands the code to the right service.

A user's *default* method is a stored preference; a login may temporarily use the
other verified destination, which never touches that preference.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.contact import OTP_METHODS, OtpMethod, Purpose, parse_otp_method
from app.db.models.user import User
from app.exceptions import ServiceUnavailableError
from app.services.email_service import EmailService
from app.services.sms_service import SmsService


@dataclass(frozen=True)
class Destination:
    """A resolved way to reach one user: a method plus the contact it needs."""

    method: OtpMethod
    address: str


class OtpDeliveryService:
    """Routes a code to the user's chosen method."""

    def __init__(self, sms: SmsService, email: EmailService) -> None:
        self._sms = sms
        self._email = email

    def is_configured(self, method: OtpMethod) -> bool:
        """Whether that provider has credentials at all."""
        return self._email.is_configured if method == "EMAIL" else self._sms.is_configured

    def destination_for(self, user: User, method: OtpMethod) -> str | None:
        """The user's verified contact for ``method``, or ``None`` if there is none."""
        if method == "EMAIL":
            return user.email or None
        # MOBILE_NUMBER is NUMBER(10): render it as the canonical 10-digit local
        # form rather than losing a leading digit.
        return f"{user.mobile_number:010d}" if user.mobile_number else None

    def default_method(self, user: User) -> OtpMethod | None:
        """The method this user's saved preference selects (``None`` if unusable).

        An unrecognised stored value returns ``None`` so the caller fails closed
        instead of quietly sending to a channel the user did not choose.
        """
        return parse_otp_method(user.preferred_otp_method)

    def alternative_method(self, user: User, method: OtpMethod) -> OtpMethod | None:
        """The *other* method, but only when it is genuinely usable right now.

        Returns ``None`` when the user has no verified contact for it or its
        provider is unconfigured, so a client is never offered a switch that could
        only fail.
        """
        for candidate in OTP_METHODS:
            if candidate == method:
                continue
            if self.destination_for(user, candidate) and self.is_configured(candidate):
                return candidate
        return None

    def resolve(self, user: User, method: OtpMethod) -> Destination:
        """Resolve a method to a destination, or fail closed.

        Missing contact means the account is in a state the application cannot
        serve — it must not silently fall back to the other channel, because the
        user chose this one.
        """
        address = self.destination_for(user, method)
        if address is None:
            raise ServiceUnavailableError("Two-step verification is unavailable for this account")
        return Destination(method=method, address=address)

    async def send(
        self, destination: Destination, *, code: str, user: User, purpose: Purpose
    ) -> None:
        """Send ``code`` through the provider ``destination.method`` names.

        ``purpose`` reaches the email wording (sign in vs confirm an address); the
        SMS template carries the code alone and ignores it.
        """
        if destination.method == "EMAIL":
            await self._email.send_otp_email(
                recipient=destination.address, code=code, user_id=user.id, purpose=purpose
            )
            return
        await self._sms.send_verify_code(
            mobile=destination.address,
            code=code,
            display_name=user.display_name,
            username=user.username,
            user_id=user.id,
        )
