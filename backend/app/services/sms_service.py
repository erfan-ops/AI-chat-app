"""SMS.ir delivery of one-time verification codes.

Provider specifics live here and nowhere else. The API key comes from settings
only, and neither the key nor the generated code is ever logged or returned.
"""

from __future__ import annotations

import logging
import re

import httpx

from app.core.config import Settings
from app.core.logging import get_logger, structured
from app.exceptions import ServiceUnavailableError

logger = get_logger("app.services.sms")

# SMS.ir template 601570 expects these parameter names.
PARAMETER_NAME_USER = "USERNAME"
PARAMETER_NAME_CODE = "CODE"

SEND_VERIFY_PATH = "/v1/send/verify"

# Status 1 means the request was accepted; everything else is a failure. The
# numbers are SMS.ir's own codes (0, 10-16, 20, 101-125) and are logged for
# diagnosis but never shown to the user.
SUCCESS_STATUS = 1

# Never forward provider text to a client, and never log the response body: it
# can echo request details.
_USER_SAFE_MESSAGE = "Could not send the verification code. Please try again."

# The display name is interpolated into a template, so strip control/bidi
# characters (they can scramble the message) and keep it short.
_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")
_MAX_NAME_LENGTH = 25


def _safe_display_name(display_name: str | None, username: str) -> str:
    candidate = (display_name or username or "").strip()
    cleaned = _UNSAFE_NAME.sub("", candidate)
    return cleaned[:_MAX_NAME_LENGTH] or username


class SmsService:
    """Sends verification codes through SMS.ir's ``/v1/send/verify`` endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.sms_ir_api_key)

    async def send_verify_code(
        self,
        *,
        mobile: str,
        code: str,
        display_name: str | None,
        username: str,
        user_id: int,
    ) -> None:
        """Send ``code`` to the local 10-digit ``mobile``. Raises on any failure."""
        if not self.is_configured:
            raise ServiceUnavailableError("SMS is not configured")

        payload = {
            "mobile": mobile,
            "templateId": self._settings.sms_ir_template_id,
            "parameters": [
                {"name": PARAMETER_NAME_USER, "value": _safe_display_name(display_name, username)},
                {"name": PARAMETER_NAME_CODE, "value": code},
            ],
        }
        url = f"{self._settings.sms_ir_base_url}{SEND_VERIFY_PATH}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.sms_ir_timeout_seconds, connect=5.0)
            ) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "x-api-key": self._settings.sms_ir_api_key,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            structured(
                logger,
                logging.WARNING,
                "sms request failed",
                user_id=user_id,
                error=type(exc).__name__,
            )
            raise ServiceUnavailableError(_USER_SAFE_MESSAGE) from exc

        status, message_id = self._parse(response, user_id=user_id)
        if response.status_code >= 400 or status != SUCCESS_STATUS:
            structured(
                logger,
                logging.WARNING,
                "sms rejected",
                user_id=user_id,
                http_status=response.status_code,
                sms_status=status,
                message_id=message_id,
            )
            raise ServiceUnavailableError(_USER_SAFE_MESSAGE)

        structured(logger, logging.INFO, "sms code sent", user_id=user_id, message_id=message_id)

    def _parse(self, response: httpx.Response, *, user_id: int) -> tuple[int, int | None]:
        """Return ``(status, messageId)``; ``status`` is -1 when unparseable."""
        try:
            body = response.json()
        except ValueError:
            structured(
                logger,
                logging.WARNING,
                "sms response was not json",
                user_id=user_id,
                http_status=response.status_code,
            )
            return -1, None
        if not isinstance(body, dict):
            return -1, None
        status = body.get("status")
        data = body.get("data")
        message_id = data.get("messageId") if isinstance(data, dict) else None
        return (status if isinstance(status, int) else -1), (
            message_id if isinstance(message_id, int) else None
        )
