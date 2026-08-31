"""MEMORIES — long-term memory per user/character pair, built for context injection."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Memory(Base):
    __tablename__ = "MEMORIES"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("USERS.id"))
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("CHARACTERS.id"))
    content: Mapped[str] = mapped_column(Text)
    memory_type: Mapped[str] = mapped_column(String(50))
    importance: Mapped[float | None] = mapped_column(Numeric(4, 3))
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20))
    message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("MESSAGES.id"))
