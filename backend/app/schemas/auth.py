"""Authentication request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.contact import OtpMethod
from app.schemas.users import USERNAME_MAX_LENGTH, USERNAME_MIN_LENGTH, USERNAME_PATTERN, UserRead


class RegisterRequest(BaseModel):
    """Body for POST /auth/register."""

    username: str = Field(
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        pattern=USERNAME_PATTERN,
    )
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)


class LoginRequest(BaseModel):
    """Body for POST /auth/login."""

    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(min_length=1, max_length=128)


class GoogleSignInRequest(BaseModel):
    """Body for POST /auth/google.

    ``credential`` is the ID token Google Identity Services hands the browser. It is
    opaque to the client: this API verifies it against Google's keys before believing
    anything in it (see app/services/google_auth_service.py).
    """

    credential: str = Field(min_length=16, max_length=8192)


class PasswordChangeRequest(BaseModel):
    """Body for POST /me/password.

    The confirmation field is the client's business, not the API's: by the time a
    request arrives there is only one new password to set.
    """

    # May be empty for an account that has no password yet (created through Google):
    # that request sets a *first* password rather than changing one, and there is
    # nothing to confirm. For an account that has one, an empty value simply fails to
    # verify — see AuthService.change_password.
    current_password: str = Field(default="", max_length=128)
    # Same rules as registration, since it is the same credential.
    new_password: str = Field(min_length=8, max_length=128)


class LoginResponse(BaseModel):
    """Successful login: a bearer access token plus the user profile."""

    model_config = ConfigDict(from_attributes=True)

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Token lifetime in minutes")
    user: UserRead


class OtpRequiredResponse(BaseModel):
    """The password was correct, but two-step verification is enabled.

    No token is issued yet, and deliberately no hint about *where* the code went:
    naming the contact would disclose the phone suffix or the address to anyone
    holding the password, which is exactly what the second factor protects against.
    The channel is disclosed because the client cannot describe the next step
    without it, and it is the weaker of the two facts.
    """

    otp_required: Literal[True] = True
    challenge_id: str
    code_expires_in_seconds: int = Field(description="Lifetime of the code, in seconds")
    delivery_method: OtpMethod = Field(description="Channel the code was sent through")
    alternative_method: OtpMethod | None = Field(
        default=None,
        description="The other usable channel, if any — absent when there is none",
    )


class LoginOtpRequest(BaseModel):
    """Body for POST /auth/login/otp — the second step of a two-step login."""

    challenge_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=6, max_length=6, pattern=r"[0-9]{6}")


class LoginOtpMethodRequest(BaseModel):
    """Body for POST /auth/login/otp/method — resend this login's code elsewhere.

    Only the channel is named: the destination always comes from the account, so a
    caller can never redirect a code to an address of their choosing.
    """

    challenge_id: str = Field(min_length=16, max_length=128)
    method: OtpMethod
