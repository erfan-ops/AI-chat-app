"""AI catalog schemas: characters (personas) and models.

Some fields are administrator-only (``ROLE_admin``). They live on the shared request
schema so that ``/characters`` stays a single endpoint, and the service rejects them
with 403 when a non-admin sends one — they are never silently ignored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Keys on the character schemas that only an administrator may send.
CHARACTER_ADMIN_FIELDS = frozenset({"owner_user_id", "status"})


def _reject_explicit_nulls(model: BaseModel, fields: tuple[str, ...]) -> None:
    """Guard the NOT NULL columns on a partial update.

    ``None`` is how these schemas spell "not provided", so a column that cannot be
    null must reject an explicitly-sent ``null`` instead of passing it to the database.
    """
    for field in fields:
        if field in model.model_fields_set and getattr(model, field) is None:
            raise ValueError(f"{field} cannot be null")


class CharacterCreate(BaseModel):
    """Body for POST /characters.

    For a normal user the owner is always the authenticated user — never the request
    body. An administrator may additionally pass ``owner_user_id`` (explicit ``null``
    creates a global/built-in character) and ``status``.
    Lengths mirror the ``CHARACTERS`` column widths.
    """

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = Field(default=None, max_length=1000)
    system_prompt: str | None = Field(
        default=None, max_length=3500, description="Persona / system prompt sent to the model"
    )
    owner_user_id: int | None = Field(
        default=None,
        ge=1,
        description="Admin only. Omit to own it yourself; null creates a global character.",
    )
    status: str | None = Field(
        default=None, min_length=1, max_length=20, description="Admin only. Defaults to ACTIVE."
    )

    @property
    def admin_fields(self) -> frozenset[str]:
        """Admin-only keys actually present in the request body."""
        return CHARACTER_ADMIN_FIELDS & self.model_fields_set


class CharacterUpdate(BaseModel):
    """Body for PATCH /characters/{id} — at least one field must be provided.

    Owners may edit ``name``/``description``/``avatar_url``/``system_prompt`` on their
    own characters; ``owner_user_id`` and ``status`` are administrator-only.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = Field(default=None, max_length=1000)
    system_prompt: str | None = Field(default=None, max_length=3500)
    owner_user_id: int | None = Field(
        default=None, ge=1, description="Admin only. null makes the character global."
    )
    status: str | None = Field(default=None, min_length=1, max_length=20, description="Admin only.")

    @model_validator(mode="after")
    def _at_least_one_field(self) -> CharacterUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        _reject_explicit_nulls(self, ("name", "status"))  # NOT NULL columns
        return self

    @property
    def admin_fields(self) -> frozenset[str]:
        """Admin-only keys actually present in the request body."""
        return CHARACTER_ADMIN_FIELDS & self.model_fields_set

    def changes(self) -> dict[str, Any]:
        """Only the fields the client actually sent (so ``null`` clears a column)."""
        return self.model_dump(exclude_unset=True)


class CharacterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    avatar_url: str | None
    system_prompt: str | None
    status: str
    created_at: datetime
    owner_user_id: int | None = Field(
        description="null for built-in characters; the creator for private ones"
    )


class ModelRead(BaseModel):
    """A model offered for conversations.

    Endpoint credentials (``AI_ENDPOINTS.BASE_URL`` / ``API_KEY``) are never exposed —
    only the endpoint's id.
    """

    id: int
    model_name: str
    display_name: str | None
    provider: str | None
    provider_id: int
    context_window: int | None
    active: bool
    endpoint_id: int
    created_at: datetime


class ModelCreate(BaseModel):
    """Body for POST /models (administrators only)."""

    provider_id: int = Field(ge=1)
    endpoint_id: int = Field(ge=1)
    model_name: str = Field(min_length=1, max_length=150)
    display_name: str | None = Field(default=None, max_length=100)
    context_window: int | None = Field(default=None, ge=1)
    active: bool = Field(default=False, description="Matches the ACTIVE 0/1 column default")


class ModelUpdate(BaseModel):
    """Body for PATCH /models/{id} (administrators only) — at least one field."""

    provider_id: int | None = Field(default=None, ge=1)
    endpoint_id: int | None = Field(default=None, ge=1)
    model_name: str | None = Field(default=None, min_length=1, max_length=150)
    display_name: str | None = Field(default=None, max_length=100)
    context_window: int | None = Field(default=None, ge=1)
    active: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> ModelUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        _reject_explicit_nulls(  # NOT NULL columns
            self, ("provider_id", "endpoint_id", "model_name", "active")
        )
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client actually sent."""
        return self.model_dump(exclude_unset=True)
