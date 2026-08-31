"""Conversation schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CharacterBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ModelBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    model_name: str
    display_name: str | None


class PersonaBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ConversationCreate(BaseModel):
    """Body for POST /conversations."""

    character_id: int = Field(ge=1)
    model_id: int | None = Field(
        default=None, ge=1, description="Defaults to the user's default model"
    )
    title: str | None = Field(default=None, min_length=1, max_length=255)
    user_persona_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Optional persona the user role-plays as. Must belong to the authenticated "
            "user. Omit for no persona — the AI then just follows the character prompt."
        ),
    )


class ConversationUpdate(BaseModel):
    """Body for PATCH /conversations/{id}."""

    title: str = Field(min_length=1, max_length=255)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    character: CharacterBrief | None
    model: ModelBrief | None
    user_persona: PersonaBrief | None = Field(
        default=None, description="null when the conversation has no persona"
    )
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None
