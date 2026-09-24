"""Two-step verification contracts (one-time codes by SMS or email)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.contact import SentOtpMethod
from app.core.email import MAX_EMAIL_LENGTH, normalize_email
from app.core.mobile import normalize_mobile


class OtpEnableRequest(BaseModel):
    """Body for POST /me/otp/enable — the contact to verify and its channel.

    Lengths are only bounded to keep the payload sane; the shape is validated by
    ``normalize_mobile`` / ``normalize_email``, which explain what is wrong instead
    of returning a schema error. ``method`` defaults to SMS, so a body written
    before email existed still means exactly what it used to.
    """

    method: SentOtpMethod = "SMS"
    mobile_number: str | None = Field(default=None, min_length=1, max_length=32)
    email: str | None = Field(default=None, min_length=3, max_length=MAX_EMAIL_LENGTH)

    @field_validator("mobile_number")
    @classmethod
    def _canonical_mobile(cls, value: str | None) -> str | None:
        """Store/compare the canonical form, never the formatted one."""
        return normalize_mobile(value) if value is not None else None

    @field_validator("email")
    @classmethod
    def _canonical_email(cls, value: str | None) -> str | None:
        return normalize_email(value) if value is not None else None

    @model_validator(mode="after")
    def _matching_destination(self) -> OtpEnableRequest:
        provided = {
            "SMS": self.mobile_number is not None,
            "EMAIL": self.email is not None,
        }
        if not provided[self.method]:
            expected = "mobile_number" if self.method == "SMS" else "email"
            raise ValueError(f"Provide {expected} when method is {self.method}")
        return self


class OtpVerifyRequest(BaseModel):
    """Body for POST /me/otp/verify.

    Carries no destination on purpose: the address that was verified is the one the
    challenge went to, never a second client-supplied value.
    """

    challenge_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=6, max_length=6, pattern=r"[0-9]{6}")


class TotpEnrollRead(BaseModel):
    """Body of POST /me/totp/enable.

    The secret and the URI are shown once, to the authenticated owner, so they can be
    scanned or typed into an authenticator app. Nothing is enabled yet: the code that
    app produces has to come back through `POST /me/otp/verify`.
    """

    challenge_id: str
    secret: str = Field(description="Base32 secret, for entering by hand")
    otpauth_uri: str = Field(description="otpauth:// URI for the QR code")
    code_expires_in_seconds: int = Field(description="How long the enrolment stays open")


class OtpChallengeRead(BaseModel):
    """A code has been sent; the challenge id ties the next call to this attempt."""

    challenge_id: str
    code_expires_in_seconds: int = Field(description="Lifetime of the code, in seconds")
    method: SentOtpMethod = Field(description="Channel the code was sent through")
    destination_hint: str = Field(description="Masked address the code was sent to")
