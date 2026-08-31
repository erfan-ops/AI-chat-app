"""USERS — user accounts and authentication data."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

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
    # Matches the DB default 'ROLE_user'; the app never inserts a value.
    role: Mapped[str] = mapped_column(String(20), server_default=text("'ROLE_user'"))

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    @property
    def is_admin(self) -> bool:
        """Exact, case-sensitive match — the role is only ever set in the database."""
        return self.role == ADMIN_ROLE
