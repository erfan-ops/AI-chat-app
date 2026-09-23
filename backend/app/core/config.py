"""Centralized application configuration.

All values come from environment variables (or a local ``.env`` file). Nothing that
is a secret has a usable default — see ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Immutable-ish application settings, validated at startup."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    app_name: str = "AI Chat API"
    app_env: str = "development"
    log_level: str = "INFO"

    # Oracle database (oracle+oracledb, thin mode — no client library required).
    database_url: str = (
        "oracle+oracledb://chatbot:chatbot@192.168.1.42:1521/?service_name=pdb.oracle.ek"
    )

    # JWT signing. Override JWT_SECRET in every deployment!
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=1440, ge=1)

    # Comma-separated list of allowed CORS origins.
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://192.168.1.69:5931"

    # AI provider mode:
    #   "database"  -> resolve provider/base_url/api_key/model from the DB (default)
    #   "mock"      -> deterministic fake provider for development/tests
    #   "openai"    -> force the OpenAI-compatible provider (DeepSeek included)
    #   "anthropic" -> force the Anthropic provider
    ai_provider: str = "database"
    # Fallback credentials used only when ai_provider != "database".
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = ""

    # Cloudinary image hosting. Avatars are uploaded straight from the browser
    # using a signature minted here, so the API secret never leaves the server
    # (and the image bytes never pass through the API). Empty values disable the
    # feature: POST /cloudinary/signature answers 503.
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    # SMS.ir one-time codes for two-step verification. An empty API key disables
    # the feature: the OTP endpoints answer 503, exactly like Cloudinary above.
    # The template is a "send verify code" template whose parameters are USERNAME
    # and CODE (see docs/otp-2fa-notes.md).
    sms_ir_api_key: str = ""
    sms_ir_template_id: int = Field(default=601570, ge=1)
    sms_ir_base_url: str = "https://api.sms.ir"
    sms_ir_timeout_seconds: float = Field(default=10.0, gt=0)

    # Resend is the *other* OTP delivery method (email). Same convention as SMS
    # above: an empty API key disables it and the endpoints answer 503 rather than
    # attempting a send that cannot work.
    resend_api_key: str = ""
    resend_from_email: str = "AI-chat@mail.erfancodes.ir"
    # Subject lines. The bodies are the two templates in ``email_service`` — a code
    # to sign in, and a code confirming an address for the first time.
    resend_otp_subject: str = "Your AI Chat verification code"
    resend_activation_subject: str = "Confirm your email address"
    # Seconds; the SDK's HTTPXClient takes an int.
    resend_timeout_seconds: int = Field(default=10, gt=0)
    # Code lifetime, resend cooldown, per-challenge attempt cap and daily send cap.
    # The cooldown is a security control, not a nicety: without it a password
    # holder could request a fresh code per guess and the attempt cap would be moot.
    otp_code_ttl_seconds: int = Field(default=120, ge=30)
    otp_resend_cooldown_seconds: int = Field(default=60, ge=0)
    otp_max_verify_attempts: int = Field(default=5, ge=1)
    otp_max_sends_per_day: int = Field(default=10, ge=1)

    ai_context_max_messages: int = Field(default=50, ge=1)
    ai_default_context_chars: int = Field(default=16000, ge=100)
    ai_max_memories: int = Field(default=5, ge=0)
    ai_temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    ai_max_tokens: int = Field(default=1024, ge=1)
    ai_stream_timeout_seconds: float = Field(default=120.0, ge=5.0)

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed list of allowed CORS origins."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
