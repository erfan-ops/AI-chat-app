"""PROVIDERS, AI_ENDPOINTS, AI_MODELS — the DB-driven AI provider configuration."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Provider(Base):
    """Catalog of AI vendors/gateways (e.g. "DeepSeek", "OpenAI", "Anthropic")."""

    __tablename__ = "PROVIDERS"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Trailing underscore: NAME is a reserved word in Oracle.
    name_: Mapped[str] = mapped_column("NAME_", String(150))

    models: Mapped[list[AIModel]] = relationship(back_populates="provider")


class AIEndpoint(Base):
    """A concrete API endpoint: base URL + API key (secret — never expose)."""

    __tablename__ = "AI_ENDPOINTS"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(500))
    api_key: Mapped[str] = mapped_column(String(255))

    models: Mapped[list[AIModel]] = relationship(back_populates="endpoint")


class AIModel(Base):
    """A model offered by a provider through an endpoint."""

    __tablename__ = "AI_MODELS"
    __table_args__ = (UniqueConstraint("provider_id", "model_name", "endpoint_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(Integer, ForeignKey("PROVIDERS.id"))
    model_name: Mapped[str] = mapped_column(String(150))
    display_name: Mapped[str | None] = mapped_column(String(100))
    context_window: Mapped[int | None] = mapped_column(Integer)
    # 0/1 flag; defaults to inactive in the DB.
    active: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    endpoint_id: Mapped[int] = mapped_column(Integer, ForeignKey("AI_ENDPOINTS.id"))

    provider: Mapped[Provider | None] = relationship(back_populates="models")
    endpoint: Mapped[AIEndpoint | None] = relationship(back_populates="models")

    @property
    def is_active(self) -> bool:
        return self.active == 1
