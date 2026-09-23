"""OTP_LOG — the history of issued one-time codes.

This is an **audit trail, not the verification store**: codes are still checked
against the in-process challenges in ``OtpService``, which is where a code's secret
and attempt budget live. A row here records that a code was issued to a user, by
which method, for what purpose, and whether it was ever used.

Rows are only written once a provider has accepted the message, so the table
describes codes that really existed rather than attempts.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class OtpLog(Base):
    __tablename__ = "OTP_LOG"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    # HMAC of the code, hex-encoded — never the code itself. The key is a per-process
    # secret, so this cannot be reversed even with the database, and it is not
    # comparable across restarts (see docs/otp-2fa-notes.md).
    code_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 0/1 flag: 0 while the code is live, 1 once it has been used. A code that expired
    # or was superseded is left at 0 — it was issued and never used, which is what
    # happened.
    consumed: Mapped[int | None] = mapped_column(Integer)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # "login" | "verify_contact" (app/core/contact.py).
    purpose: Mapped[str | None] = mapped_column(String(100))
    # "SMS" | "EMAIL".
    method: Mapped[str | None] = mapped_column(String(20))
