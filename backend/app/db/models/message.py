"""MESSAGES and MESSAGE_GENERATIONS — chat messages and per-generation telemetry."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.conversation import Conversation


class Message(Base):
    __tablename__ = "MESSAGES"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(Integer, ForeignKey("CONVERSATIONS.id"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reply_to_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("MESSAGES.id"))

    conversation: Mapped[Conversation] = relationship()

    generations: Mapped[list[MessageGeneration]] = relationship(
        back_populates="message", order_by="MessageGeneration.id"
    )


class MessageGeneration(Base):
    """Telemetry/audit row for one AI generation (tokens, latency, purpose)."""

    __tablename__ = "MESSAGE_GENERATIONS"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, ForeignKey("MESSAGES.id"))
    model_id: Mapped[int] = mapped_column(Integer, ForeignKey("AI_MODELS.id"))
    purpose: Mapped[str] = mapped_column(String(50))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    temperature: Mapped[float | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = mapped_column(DateTime)

    message: Mapped[Message] = relationship(back_populates="generations")
