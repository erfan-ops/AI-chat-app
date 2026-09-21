"""Two-step verification contracts (SMS one-time codes)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.core.mobile import normalize_mobile


class OtpEnableRequest(BaseModel):
    """Body for POST /me/otp/enable — the local 10-digit mobile number.

    Length is only bounded to keep the payload sane; the shape is validated by
    ``normalize_mobile``, which explains what is wrong instead of returning a
    schema error.
    """

    mobile_number: str = Field(min_length=1, max_length=32)

    @field_validator("mobile_number")
    @classmethod
    def _canonical(cls, value: str) -> str:
        """Store/compare the canonical form, never the formatted one."""
        return normalize_mobile(value)


class OtpVerifyRequest(BaseModel):
    """Body for POST /me/otp/verify."""

    challenge_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=6, max_length=6, pattern=r"[0-9]{6}")


class OtpChallengeRead(BaseModel):
    """A code has been sent; the challenge id ties the next call to this attempt."""

    challenge_id: str
    code_expires_in_seconds: int = Field(description="Lifetime of the code, in seconds")
    mobile_hint: str = Field(description="Masked number the code was sent to")
