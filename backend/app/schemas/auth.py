"""Authentication request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.users import UserRead

USERNAME_PATTERN = r"^[A-Za-z0-9_.-]+$"


class RegisterRequest(BaseModel):
    """Body for POST /auth/register."""

    username: str = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)


class LoginRequest(BaseModel):
    """Body for POST /auth/login."""

    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """Successful login: a bearer access token plus the user profile."""

    model_config = ConfigDict(from_attributes=True)

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Token lifetime in minutes")
    user: UserRead


class OtpRequiredResponse(BaseModel):
    """The password was correct, but two-step verification is enabled.

    No token is issued yet, and deliberately no hint about which number the code
    went to: that would disclose the phone suffix to anyone holding the password,
    which is exactly what the second factor protects against.
    """

    otp_required: Literal[True] = True
    challenge_id: str
    code_expires_in_seconds: int = Field(description="Lifetime of the code, in seconds")


class LoginOtpRequest(BaseModel):
    """Body for POST /auth/login/otp — the second step of a two-step login."""

    challenge_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=6, max_length=6, pattern=r"[0-9]{6}")
