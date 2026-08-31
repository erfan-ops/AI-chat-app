"""CHARACTERS — AI characters/personas. DESCRIPTION holds the persona system prompt."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Character(Base):
    __tablename__ = "CHARACTERS"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    system_prompt: Mapped[str | None] = mapped_column(String(9000))
    # NULL = built-in character visible to everyone; otherwise the creating user.
    owner_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("USERS.id"))

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"
