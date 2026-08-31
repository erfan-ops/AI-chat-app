"""CONVERSATIONS — one conversation per user/character pair, pinned to a model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.ai import AIModel
from app.db.models.character import Character
from app.db.models.persona import UserPersona


class Conversation(Base):
    __tablename__ = "CONVERSATIONS"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("USERS.id"))
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("CHARACTERS.id"))
    model_id: Mapped[int] = mapped_column(Integer, ForeignKey("AI_MODELS.id"))
    title: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime)
    # NULL = the user picked no persona; the AI just follows the character prompt.
    user_persona_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("USER_PERSONAS.id"))

    character: Mapped[Character | None] = relationship()
    model: Mapped[AIModel | None] = relationship()
    user_persona: Mapped[UserPersona | None] = relationship()
