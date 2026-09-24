"""USERS — user accounts and authentication data."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.contact import stored_secret
from app.db.database import Base

# ROLE values. The DB default is ROLE_user; no endpoint ever writes ROLE.
USER_ROLE = "ROLE_user"
ADMIN_ROLE = "ROLE_admin"


class User(Base):
    __tablename__ = "USERS"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    # Argon2id PHC string — the application is the source of the hashing scheme.
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(100))
    default_model_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("AI_MODELS.id"))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Verified mobile as the 10-digit local Iranian number (NUMBER(10,0) in
    # Oracle — the +98 form is presentation only and does not fit). Unique
    # (UK_USERS_MOBILE_NUMBER): one account per number, so two accounts can never
    # receive each other's codes.
    mobile_number: Mapped[int | None] = mapped_column(Integer, unique=True)
    # 0/1 flag, NOT NULL DEFAULT 0 in Oracle: 1 = a code is required at login.
    otp_enabled: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # Verified email address, stored canonical (lowercase ASCII) — see
    # app/core/email.py. Only ever written after a code sent to it came back.
    # Unique (UK_USERS_EMAIL) for the same reason as the mobile number.
    email: Mapped[str | None] = mapped_column(String(200), unique=True)
    # Authenticator (TOTP) shared secret, Base32, 32 characters — CHAR(32) in Oracle,
    # which blank-pads, so it is stripped when read. Generated on enrolment and only
    # trusted once a code from it has been verified; never returned by the API.
    totp_secret: Mapped[str | None] = mapped_column(String(32))
    # Which channel login codes go to: 'SMS' | 'EMAIL' | 'TOTP' (app/core/contact.py). The
    # column is nullable with no default, so NULL means SMS — every account that
    # enabled two-step verification before email existed stays an SMS account.
    preferred_otp_method: Mapped[str | None] = mapped_column(String(20))
    # Matches the DB default 'ROLE_user'; the app never inserts a value.
    role: Mapped[str] = mapped_column(String(20), server_default=text("'ROLE_user'"))

    @property
    def authenticator_enrolled(self) -> bool:
        """Whether an authenticator app has a secret stored for this account.

        The answer, never the secret: it exists so a client can offer the method
        without being told anything it could use to generate a code. ``GET /me`` is
        the only place it is exposed, and only to the account's owner.
        """
        return stored_secret(self.totp_secret) is not None

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    @property
    def is_admin(self) -> bool:
        """Exact, case-sensitive match — the role is only ever set in the database."""
        return self.role == ADMIN_ROLE
