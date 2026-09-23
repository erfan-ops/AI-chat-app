"""User schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Username rules, shared by registration and profile updates so the two cannot
# drift. The DB column is VARCHAR2(100) but the application is stricter.
USERNAME_PATTERN = r"^[A-Za-z0-9_.-]+$"
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32


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
    # Verified mobile number, as the canonical 10-digit local form.
    mobile_number: str | None = None
    # True once two-step verification is active (SMS code required at login).
    otp_enabled: bool = False

    @field_validator("mobile_number", mode="before")
    @classmethod
    def _format_mobile(cls, value: object) -> object:
        """Render the NUMBER(10) column as a string so no digit is ever dropped."""
        if isinstance(value, int) and not isinstance(value, bool):
            return f"{value:010d}"
        return value


class UserUpdate(BaseModel):
    """Body for PATCH /me — at least one field must be provided.

    ``username`` follows the same rules as registration; uniqueness is enforced
    by the database and reported as 409.
    """

    username: str | None = Field(
        default=None,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        pattern=USERNAME_PATTERN,
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    default_model_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UserUpdate:
        if self.username is None and self.display_name is None and self.default_model_id is None:
            raise ValueError("Provide at least one of: username, display_name, default_model_id")
        return self
