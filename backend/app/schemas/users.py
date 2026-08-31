"""User schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UserRead(BaseModel):
    """Public user profile. Never contains the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None
    role: str
    status: str
    default_model_id: int | None
    created_at: datetime
    last_login_at: datetime | None


class UserUpdate(BaseModel):
    """Body for PATCH /me — at least one field must be provided."""

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    default_model_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UserUpdate:
        if self.display_name is None and self.default_model_id is None:
            raise ValueError("Provide at least one of: display_name, default_model_id")
        return self
