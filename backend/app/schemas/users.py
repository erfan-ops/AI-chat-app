"""User schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.contact import DEFAULT_OTP_METHOD, OtpMethod, parse_otp_method

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
    # Verified email address (canonical lowercase).
    email: str | None = None
    # True once two-step verification is active (a code is required at login).
    otp_enabled: bool = False
    # Channel login codes go to. The column is nullable, so NULL reads as SMS.
    preferred_otp_method: OtpMethod = DEFAULT_OTP_METHOD
    # Whether an authenticator app is enrolled — the answer, never the secret. A
    # client needs it to offer the method; nothing here can produce a code.
    authenticator_enrolled: bool = False
    # Profile picture: the Cloudinary URL the client uploaded. Resized at delivery
    # (`utils/cloudinary.ts`), so this is the master, not a per-size variant.
    avatar_url: str | None = None

    @field_validator("mobile_number", mode="before")
    @classmethod
    def _format_mobile(cls, value: object) -> object:
        """Render the NUMBER(10) column as a string so no digit is ever dropped."""
        if isinstance(value, int) and not isinstance(value, bool):
            return f"{value:010d}"
        return value

    @field_validator("preferred_otp_method", mode="before")
    @classmethod
    def _default_method(cls, value: object) -> object:
        """An unset or unrecognised column value reads as SMS — what it has always meant.

        A value this application does not know cannot happen through the API, but a
        hand-edited row must not turn `GET /me` into a 500. Where such a value
        actually matters — sending a login code — the flow fails closed (503) rather
        than delivering to a channel the user did not choose.
        """
        return parse_otp_method(value if isinstance(value, str) else None) or DEFAULT_OTP_METHOD


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
    # Only accepted for a channel that has a verified destination — the service
    # rejects the rest, so a login code can never point at a contact that does not
    # exist.
    preferred_otp_method: OtpMethod | None = None
    # The uploaded Cloudinary URL. An explicit `null` removes the picture, which is why
    # the *only* way to leave it alone is to omit the field — this column, unlike
    # `default_model_id`, is meant to be clearable.
    avatar_url: str | None = Field(default=None, max_length=1000)

    @property
    def provided(self) -> frozenset[str]:
        """The fields the client actually sent (an explicit ``null`` clears one)."""
        return frozenset(self.model_fields_set)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UserUpdate:
        if not self.model_fields_set:
            raise ValueError(
                "Provide at least one of: username, display_name, default_model_id, "
                "preferred_otp_method, avatar_url"
            )
        return self
