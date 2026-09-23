"""Data access for OTP_LOG (the issued-code history)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.otp_log import OtpLog


class OtpLogRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self,
        *,
        user_id: int,
        purpose: str,
        method: str,
        code_hash: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> OtpLog:
        """Stage an issued code; the caller commits."""
        entry = OtpLog(
            user_id=user_id,
            purpose=purpose,
            method=method,
            code_hash=code_hash,
            created_at=created_at,
            expires_at=expires_at,
            consumed=0,
        )
        self._db.add(entry)
        await self._db.flush()
        return entry

    async def mark_consumed(
        self, *, user_id: int, purpose: str, consumed_at: datetime
    ) -> OtpLog | None:
        """Flag the newest unused row for that user and purpose as used.

        The table has no challenge id, so the row is found by what it can be matched
        on. That is sound because the OTP service keeps **one live challenge per
        user**: the newest unused row for a purpose is the code that was just
        verified. Older unused rows stay as they are — those codes were superseded
        or expired, and "issued, never used" is what happened to them.

        Returns the row that was updated, or ``None`` when there is nothing to match
        (a code issued before this table existed, for instance).
        """
        stmt = (
            select(OtpLog)
            .where(
                OtpLog.user_id == user_id,
                OtpLog.purpose == purpose,
                OtpLog.consumed == 0,
            )
            .order_by(OtpLog.created_at.desc(), OtpLog.id.desc())
            .limit(1)
        )
        entry = (await self._db.scalars(stmt)).first()
        if entry is None:
            return None
        entry.consumed = 1
        entry.consumed_at = consumed_at
        await self._db.flush()
        return entry
