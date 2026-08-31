"""User-persona schemas — who the user is role-playing as.

Only ``name`` is required; the rest of the columns are nullable and a persona with
nothing but a name is valid. Field limits mirror the ``USER_PERSONAS`` column widths
(``AGE`` is ``NUMBER(2,0)``, so at most two digits).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PersonaCreate(BaseModel):
    """Body for POST /personas. The owner always comes from the access token."""

    name: str = Field(min_length=1, max_length=100)
    gender: str | None = Field(default=None, max_length=10)
    description: str | None = Field(default=None, max_length=1500)
    age: int | None = Field(default=None, ge=0, le=99)


class PersonaUpdate(BaseModel):
    """Body for PATCH /personas/{id} — at least one field must be provided."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    gender: str | None = Field(default=None, max_length=10)
    description: str | None = Field(default=None, max_length=1500)
    age: int | None = Field(default=None, ge=0, le=99)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> PersonaUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")  # NOT NULL column
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client actually sent (so ``null`` clears a column)."""
        return self.model_dump(exclude_unset=True)


class PersonaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    gender: str | None
    description: str | None
    age: int | None
