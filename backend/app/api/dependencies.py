"""FastAPI dependencies: settings, sessions, the authenticated user, services."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.registry import create_provider
from app.core.config import Settings, get_settings
from app.core.security import InvalidTokenError, TokenManager
from app.db.database import SessionFactory
from app.db.models.user import User
from app.db.repositories.users import UserRepository
from app.exceptions import ForbiddenError, UnauthorizedError
from app.services.ai_service import AIService, ProviderFactory
from app.services.auth_service import AuthService
from app.services.cloudinary_service import CloudinaryService
from app.services.email_service import EmailService
from app.services.otp_audit import OtpAudit
from app.services.otp_delivery import OtpDeliveryService
from app.services.otp_service import OtpService
from app.services.sms_service import SmsService
from app.services.time_service import TimeService
from app.services.totp_service import TotpService

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token from POST /auth/login")


@lru_cache
def get_token_manager(settings: Settings) -> TokenManager:
    return TokenManager(
        settings.jwt_secret, settings.jwt_algorithm, settings.access_token_expire_minutes
    )


@lru_cache
def get_auth_service(settings: Settings) -> AuthService:
    return AuthService(settings)


@lru_cache
def get_ai_service(settings: Settings) -> AIService:
    return AIService(settings)


@lru_cache
def get_cloudinary_service(settings: Settings) -> CloudinaryService:
    return CloudinaryService(settings)


@lru_cache
def get_sms_service(settings: Annotated[Settings, Depends(get_settings)]) -> SmsService:
    """Overridable in tests, so no test can reach the real SMS provider.

    The ``Depends`` on ``settings`` matters: without it FastAPI treats a
    Pydantic-model parameter as a request *body* field when this is used as a
    dependency.
    """
    return SmsService(settings)


@lru_cache
def get_email_service(settings: Annotated[Settings, Depends(get_settings)]) -> EmailService:
    """Overridable in tests, so no test can reach the real email provider.

    The ``Depends`` on ``settings`` matters here for the same reason as above.
    """
    return EmailService(settings)


def get_time_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TimeService:
    """The application's corrected clock — one per process, since its offset is state.

    Deliberately not cached here: ``TimeService.instance`` is the single owner of that
    instance, so the clock the synchronization loop fills is the clock a request
    reads, no matter how this is called. See ``TimeService.instance``.
    """
    return TimeService.instance(settings)


def get_totp_service(
    time_service: Annotated[TimeService, Depends(get_time_service)],
) -> TotpService:
    """Authenticator codes. Stateless; the clock is injected."""
    return TotpService(time_service)


def get_otp_audit(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> OtpAudit:
    """OTP history writer. Deliberately not session-scoped: see app/services/otp_audit.py."""
    return OtpAudit(session_factory)


def get_otp_delivery_service(
    sms: Annotated[SmsService, Depends(get_sms_service)],
    email: Annotated[EmailService, Depends(get_email_service)],
) -> OtpDeliveryService:
    """Composes the two providers; overrides of either one are respected.

    Deliberately not cached: it holds no state, and a cache would have to be keyed
    on the (possibly overridden) services it is built from.
    """
    return OtpDeliveryService(sms, email)


@lru_cache
def get_otp_service(settings: Annotated[Settings, Depends(get_settings)]) -> OtpService:
    """In-process challenge store; overridable in tests (see conftest)."""
    return OtpService(settings)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory for flows that manage their own short sessions (AI streaming)."""
    return SessionFactory


def get_provider_factory() -> ProviderFactory:
    """Provider factory (overridable in tests to inject a scripted provider)."""
    return create_provider


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> User:
    """Resolve the authenticated user from the Bearer token.

    Uses its own short-lived session (not the request-scoped one) so that
    streaming routes never hold a database connection open for the stream.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Not authenticated")
    try:
        user_id = get_token_manager(settings).decode_user_id(credentials.credentials)
    except InvalidTokenError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc
    async with session_factory() as db:
        user = await UserRepository(db).get_by_id(user_id)
    if user is None:
        raise UnauthorizedError("Invalid or expired token")
    if not user.is_active:
        raise ForbiddenError("Account is disabled")
    request.state.user_id = user.id  # picked up by the access-log middleware
    return user


async def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Authenticated user whose stored role is ROLE_admin.

    Unauthenticated callers still get the 401 from ``get_current_user``; an
    authenticated non-admin gets 403. The role comes from the database row, never
    from the request.
    """
    if not user.is_admin:
        raise ForbiddenError("Administrator privileges required")
    return user
