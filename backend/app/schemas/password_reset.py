"""Password-recovery request/response schemas.

The three steps have three separate contracts, and the middle one hands back an
*authorization* rather than a session: it is the only thing that may be presented to
the last one. Nothing here carries a claim about what happened in a previous step —
see `app/services/password_reset_service.py`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.contact import OtpMethod
from app.core.email import MAX_EMAIL_LENGTH

# Same rules as registration and login: it is the same credential.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class PasswordResetRequest(BaseModel):
    """Body for POST /auth/password-reset/request.

    ``identifier`` is the username or the account's verified email address.
    ``method`` is optional: omitting it asks only which methods the account can be
    recovered by, which is the first screen of the flow.
    """

    identifier: str = Field(
        min_length=1,
        max_length=MAX_EMAIL_LENGTH,
        description="Username, or the account's verified email address",
    )
    method: OtpMethod | None = Field(
        default=None,
        description="Channel to send the code through; omit to only list the options",
    )


class PasswordResetOptionsRead(BaseModel):
    """What a recovery attempt may do next.

    The message is deliberately the same whether or not the account exists, and no
    destination is ever named: the caller has proved nothing about the account yet.
    """

    methods: list[OtpMethod] = Field(
        description="Methods this account can be recovered with, best-effort",
    )
    method: OtpMethod | None = Field(
        default=None, description="Channel a code was sent through, when one was sent"
    )
    challenge_id: str | None = Field(
        default=None, description="Present only when a code was actually sent"
    )
    code_expires_in_seconds: int | None = Field(
        default=None, description="Lifetime of the code, in seconds"
    )
    message: str


class PasswordResetVerifyRequest(BaseModel):
    """Body for POST /auth/password-reset/verify — the code step."""

    challenge_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=6, max_length=6, pattern=r"[0-9]{6}")


class PasswordResetAuthorizationRead(BaseModel):
    """The proof that the code was verified, and how long it lasts.

    This is not an access token and is accepted by no other endpoint: it authorizes
    one password change, for one account, for a few minutes.
    """

    reset_token: str
    expires_in_seconds: int = Field(description="How long the authorization stays usable")


class PasswordResetCompleteRequest(BaseModel):
    """Body for POST /auth/password-reset/complete."""

    reset_token: str = Field(
        min_length=16,
        max_length=256,
        description="The authorization from POST /auth/password-reset/verify",
    )
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
