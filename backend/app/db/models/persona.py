"""USER_PERSONAS — who the *user* is role-playing as, per conversation.

The character's SYSTEM_PROMPT describes the AI; a persona describes the human on
the other side. Only NAME is required — a persona may carry nothing else.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class UserPersona(Base):
    __tablename__ = "USER_PERSONAS"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("USERS.id"))
    name: Mapped[str] = mapped_column(String(100))
    gender: Mapped[str | None] = mapped_column(String(10))
    description: Mapped[str | None] = mapped_column(String(1500))
    # NUMBER(2,0) in Oracle — at most two digits.
    age: Mapped[int | None] = mapped_column(Integer)
