"""Password recovery: request a code, verify it, then set a new password.

The three steps are separate on purpose. Verification changes nothing — it returns an
authorization — and the only thing the completion endpoint accepts as proof is that
authorization, which this API minted and still remembers. A client cannot skip the
code by calling the last endpoint directly, and cannot talk it into believing the code
was right: nothing it sends is read as a statement about the previous step.

Every endpoint here is public (the user cannot sign in — that is why they are here),
so every rejection is 400-level and every answer is written to disclose as little as
possible about whether an account exists.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    client_address,
    get_auth_service,
    get_otp_audit,
    get_otp_delivery_service,
    get_otp_service,
    get_password_manager,
    get_password_reset_service,
    get_totp_service,
)
from app.core.config import Settings, get_settings
from app.core.security import PasswordManager
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.password_reset import (
    PasswordResetAuthorizationRead,
    PasswordResetCompleteRequest,
    PasswordResetOptionsRead,
    PasswordResetRequest,
    PasswordResetVerifyRequest,
)
from app.schemas.users import UserRead
from app.services.otp_audit import OtpAudit
from app.services.otp_delivery import OtpDeliveryService
from app.services.otp_service import OtpService
from app.services.password_reset_service import PasswordResetService, RecoveryOptions
from app.services.totp_service import TotpService

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])


def _options(result: RecoveryOptions) -> PasswordResetOptionsRead:
    return PasswordResetOptionsRead(
        methods=list(result.methods),
        method=result.method,
        challenge_id=result.challenge_id,
        code_expires_in_seconds=result.code_expires_in_seconds,
        message=result.message,
    )


@router.post(
    "/request",
    response_model=PasswordResetOptionsRead,
    summary="Start a password reset, and optionally send a code",
    description=(
        "Looks up the account named by `identifier` (a username, or the account's "
        "verified email address) and answers with the methods it can be recovered "
        "through. Calling it without `method` only reports those options; calling it "
        "with one sends a code through that channel and returns the `challenge_id` "
        "the next step needs.\n\n"
        "The answer is the same whether or not the account exists, and it never names "
        "a destination — no phone suffix, no address — so it cannot be used to find "
        "out who has an account here. A 429 is the one exception, and it is about the "
        "caller's own request rate.\n\n"
        "Recovery by authenticator app uses the secret already enrolled for two-step "
        "verification; nothing is sent, and no code is emailed or texted."
    ),
    responses={
        429: {"description": "Too many codes requested; retry after the given delay"},
        503: {"description": "That channel is unavailable for this account"},
    },
)
async def request_password_reset(
    body: PasswordResetRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    resets: Annotated[PasswordResetService, Depends(get_password_reset_service)],
    delivery: Annotated[OtpDeliveryService, Depends(get_otp_delivery_service)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
    audit: Annotated[OtpAudit, Depends(get_otp_audit)],
    client_ip: Annotated[str | None, Depends(client_address)],
) -> PasswordResetOptionsRead:
    result = await resets.request(
        db,
        identifier=body.identifier,
        method=body.method,
        delivery=delivery,
        otp=otp,
        audit=audit,
        client_ip=client_ip,
    )
    return _options(result)


@router.post(
    "/verify",
    response_model=PasswordResetAuthorizationRead,
    summary="Verify a recovery code and obtain a reset authorization",
    description=(
        "Exchanges the `challenge_id` from `/request` plus the code that was sent for a "
        "short-lived, single-use `reset_token`. This endpoint does **not** change the "
        "password and this token is not a session: it is accepted by `/complete` alone "
        "and grants no access to the API.\n\n"
        "The code is checked here, server-side, against the challenge this API issued — "
        "a client cannot assert that verification succeeded. An incorrect or expired "
        "code is rejected with 400 (never 401), and the challenge dies after a small "
        "number of attempts."
    ),
    responses={
        400: {"description": "The code is incorrect or has expired"},
        429: {"description": "Too many incorrect authenticator codes; try again later"},
        503: {"description": "Authenticator codes cannot be verified right now"},
    },
)
async def verify_password_reset(
    body: PasswordResetVerifyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    resets: Annotated[PasswordResetService, Depends(get_password_reset_service)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
    totp: Annotated[TotpService, Depends(get_totp_service)],
    audit: Annotated[OtpAudit, Depends(get_otp_audit)],
) -> PasswordResetAuthorizationRead:
    authorization = await resets.verify(
        db, challenge_id=body.challenge_id, code=body.code, otp=otp, totp=totp, audit=audit
    )
    return PasswordResetAuthorizationRead(
        reset_token=authorization.token, expires_in_seconds=authorization.expires_in_seconds
    )


@router.post(
    "/complete",
    response_model=UserRead,
    summary="Set a new password with a reset authorization",
    description=(
        "Sets the new password for the account the `reset_token` was issued to. The "
        "authorization is the *only* accepted proof — there is no request field, cookie "
        "or header that can stand in for it — it expires ten minutes after the code was "
        "verified, and it is spent by the first successful call, so the same token can "
        "never set a second password.\n\n"
        "The password is hashed exactly as it is at registration and at "
        "`POST /me/password`. Outstanding sign-in codes and any other reset "
        "authorization for the account are discarded at the same time.\n\n"
        "Sessions are stateless JWTs and are not revoked by this — see "
        "`docs/password-reset-notes.md` for what that does and does not mean."
    ),
    responses={
        400: {"description": "The reset authorization is unknown, expired or already used"},
    },
)
async def complete_password_reset(
    body: PasswordResetCompleteRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    resets: Annotated[PasswordResetService, Depends(get_password_reset_service)],
    passwords: Annotated[PasswordManager, Depends(get_password_manager)],
    otp: Annotated[OtpService, Depends(get_otp_service)],
) -> User:
    user = await resets.complete(
        db, token=body.reset_token, new_password=body.new_password, passwords=passwords, otp=otp
    )
    # The failures the user accumulated guessing the password they just replaced must
    # not keep them out of the new one.
    get_auth_service(settings).clear_login_throttle(user.username)
    return user
