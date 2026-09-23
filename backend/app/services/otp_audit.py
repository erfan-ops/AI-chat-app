"""Recording issued codes in ``OTP_LOG`` — best effort, never load-bearing.

The history is written around the authentication flow, not inside it: a code that
has already been sent must still let the user sign in if a log write fails, so
every failure here is logged server-side and swallowed. The alternative — failing a
login because an audit table is full — trades a working product for a record of it.

Each write takes its **own short-lived session** rather than the request's. Sharing
one would make the record part of the authentication transaction (so a rollback
could erase it) and, worse, let a failed audit write poison the session the flow is
still using. This mirrors how ``get_current_user`` reads the user in its own session
instead of the request's. It also means the row is committed as soon as the code is
sent, which is what makes the history survive a user abandoning the flow.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.contact import OtpMethod, Purpose
from app.core.logging import get_logger, structured
from app.core.time import utcnow
from app.db.repositories.otp_log import OtpLogRepository
from app.services.otp_service import Challenge

logger = get_logger("app.services.otp_audit")


class OtpAudit:
    """Writes the two events that matter: a code was issued, a code was used."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def issued(self, *, challenge: Challenge, method: OtpMethod, ttl_seconds: int) -> None:
        """Record a code that a provider has accepted and is on its way."""
        created_at = utcnow()
        try:
            async with self._session_factory() as db:
                await OtpLogRepository(db).add(
                    user_id=challenge.user_id,
                    purpose=challenge.purpose,
                    method=method,
                    # Hex, not the raw digest: the column is a VARCHAR2. Only the HMAC
                    # is stored, never the code — see app/db/models/otp_log.py.
                    code_hash=challenge.code_hash.hex(),
                    created_at=created_at,
                    expires_at=created_at + timedelta(seconds=ttl_seconds),
                )
                await db.commit()
        except SQLAlchemyError as exc:
            structured(
                logger,
                logging.WARNING,
                "could not record issued code",
                user_id=challenge.user_id,
                purpose=challenge.purpose,
                error=type(exc).__name__,
            )

    async def consumed(self, *, user_id: int, purpose: Purpose) -> None:
        """Record that a code was used, now that the account change is committed."""
        entry = None
        try:
            async with self._session_factory() as db:
                entry = await OtpLogRepository(db).mark_consumed(
                    user_id=user_id, purpose=purpose, consumed_at=utcnow()
                )
                await db.commit()
        except SQLAlchemyError as exc:
            structured(
                logger,
                logging.WARNING,
                "could not record consumed code",
                user_id=user_id,
                purpose=purpose,
                error=type(exc).__name__,
            )
            return
        if entry is None:
            # A code verified with no matching row: only possible for a challenge that
            # predates this table, or if the row was pruned. Worth knowing about.
            structured(
                logger, logging.INFO, "no otp_log row to consume", user_id=user_id, purpose=purpose
            )
