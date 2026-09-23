"""Email one-time codes through Resend.

Provider specifics live here and nowhere else. The API key comes from settings
only, and neither the key, the code, nor the recipient address is ever logged or
returned — a log line carries the user id and Resend's own error code instead.

The message bodies are the two templates below: one for confirming an address for
the first time, one for a sign-in code. They are written as plain HTML with inline
styles and a table layout, because that is what mail clients actually render —
no external stylesheet, no images, no web fonts. A ``<style>`` block adds dark-mode
colours and tighter padding on small screens where the client supports it; every
rule there is an override, so a client that ignores it still gets a readable light
email. Nothing in the markup is user-supplied, so there is nothing to escape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import resend
from resend.exceptions import NoContentError, ResendError

from app.core.config import Settings
from app.core.contact import Purpose
from app.core.logging import get_logger, structured
from app.exceptions import ServiceUnavailableError

logger = get_logger("app.services.email")

# The only text ever forwarded to a client when a send fails: provider detail stays
# server-side, exactly like the SMS service.
_USER_SAFE_MESSAGE = "Could not send the verification code. Please try again."

# The product's own palette (frontend/src/styles/tokens.css), so the email looks
# like the app that sent it.
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


@dataclass(frozen=True)
class OtpEmailCopy:
    """Everything that differs between the two emails — the words, not the layout."""

    subject: str
    preheader: str
    heading: str
    intro: str
    footnote: str


# Confirming an address is not the same act as signing in, and a code that arrives
# saying "verification code" for both is how people approve the wrong thing.
_COPY: dict[Purpose, OtpEmailCopy] = {
    "login": OtpEmailCopy(
        subject="Your AI Chat verification code",
        preheader="Your one-time code for signing in to AI Chat.",
        heading="Your sign-in code",
        intro="Enter this code to finish signing in to AI Chat.",
        footnote=(
            "If you didn't try to sign in, someone may have your password — "
            "change it, and don't share this code with anyone."
        ),
    ),
    "verify_contact": OtpEmailCopy(
        subject="Confirm your email address",
        preheader="Your one-time code for adding this address to AI Chat.",
        heading="Confirm your email address",
        intro="Enter this code in AI Chat to finish adding this address to your account.",
        footnote=(
            "If you didn't add this address, you can ignore this email — "
            "your account is unchanged and codes will not be sent here."
        ),
    ),
}


def _expiry_text(seconds: int) -> str:
    """``300`` → ``5 minutes``. Read from settings, so the email never lies."""
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"


def render_otp_email(*, purpose: Purpose, code: str, ttl_seconds: int) -> str:
    """The full HTML document for ``purpose``. Pure, so it is easy to eyeball."""
    copy = _COPY[purpose]
    expiry = _expiry_text(ttl_seconds)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{copy.subject}</title>
<style>
  @media (prefers-color-scheme: dark) {{
    .bg {{ background:#0c0f14 !important; }}
    .card {{ background:#141821 !important; border-color:#262c39 !important; }}
    .wordmark, .muted {{ color:#8b93a5 !important; }}
    .heading {{ color:#f2f4f8 !important; }}
    .body {{ color:#b9c0cf !important; }}
    .codebox {{ background:#1a1f2b !important; border-color:#262c39 !important; }}
    .code {{ color:#a5a8fb !important; }}
    .rule {{ border-color:#262c39 !important; }}
  }}
  @media only screen and (max-width:480px) {{
    .pad {{ padding-left:24px !important; padding-right:24px !important; }}
    .code {{ font-size:28px !important; letter-spacing:8px !important; padding-left:8px !important; }}
  }}
</style>
</head>
<body class="bg" style="margin:0;padding:0;background:#edf0f5;">
<div style="display:none;font-size:1px;color:#edf0f5;max-height:0;max-width:0;opacity:0;overflow:hidden;">{copy.preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="bg" bgcolor="#edf0f5" style="background:#edf0f5;">
  <tr>
    <td align="center" class="bg" bgcolor="#edf0f5" style="padding:32px 16px;background:#edf0f5;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="card" bgcolor="#ffffff" style="max-width:480px;background:#ffffff;border:1px solid #e1e5ee;border-radius:16px;">
        <tr>
          <td class="pad" style="padding:36px 36px 0;">
            <p class="wordmark" style="margin:0;font-family:{_FONT};font-size:12px;font-weight:650;letter-spacing:0.14em;text-transform:uppercase;color:#6f788c;">AI Chat</p>
            <h1 class="heading" style="margin:18px 0 0;font-family:{_FONT};font-size:22px;font-weight:650;line-height:1.3;letter-spacing:-0.01em;color:#181c26;">{copy.heading}</h1>
            <p class="body" style="margin:10px 0 0;font-family:{_FONT};font-size:15px;line-height:1.6;color:#4c5466;">{copy.intro}</p>
          </td>
        </tr>
        <tr>
          <td class="pad" style="padding:24px 36px 0;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td class="codebox" align="center" style="background:#f4f6fa;border:1px solid #e1e5ee;border-radius:12px;padding:20px 12px;">
                  <span class="code" style="font-family:{_FONT};font-size:32px;font-weight:700;letter-spacing:10px;padding-left:10px;color:#4f46e5;">{code}</span>
                </td>
              </tr>
            </table>
            <p class="muted" style="margin:12px 0 0;text-align:center;font-family:{_FONT};font-size:13px;line-height:1.5;color:#6f788c;">Expires in {expiry}.</p>
          </td>
        </tr>
        <tr>
          <td class="pad" style="padding:26px 36px 36px;">
            <hr class="rule" style="border:none;border-top:1px solid #e1e5ee;margin:0 0 16px;">
            <p class="muted" style="margin:0;font-family:{_FONT};font-size:12.5px;line-height:1.6;color:#6f788c;">{copy.footnote}</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


class EmailService:
    """Sends verification codes by email. One instance per process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if self.is_configured:
            # ``resend.api_key`` and the async client are module-level globals the
            # SDK also populates from the environment at import. Assigning them from
            # our settings is what keeps the key in the configuration system (and
            # keeps a developer's RESEND_API_KEY out of the test run).
            resend.api_key = settings.resend_api_key
            resend.default_async_http_client = resend.HTTPXClient(
                timeout=settings.resend_timeout_seconds
            )

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.resend_api_key)

    def subject_for(self, purpose: Purpose) -> str:
        """The subject line for ``purpose``; the sign-in one is configurable."""
        if purpose == "login":
            return self._settings.resend_otp_subject
        return self._settings.resend_activation_subject

    async def send_otp_email(
        self, *, recipient: str, code: str, user_id: int, purpose: Purpose
    ) -> None:
        """Send ``code`` to ``recipient``. Raises ``ServiceUnavailableError`` on failure."""
        if not self.is_configured:
            raise ServiceUnavailableError("Email is not configured")

        params: resend.Emails.SendParams = {
            "from": self._settings.resend_from_email,
            "to": recipient,
            "subject": self.subject_for(purpose),
            # The body is built here. "html" and "template" are mutually exclusive
            # at the API level, so exactly one of them may ever be passed.
            "html": render_otp_email(
                purpose=purpose, code=code, ttl_seconds=self._settings.otp_code_ttl_seconds
            ),
        }

        try:
            response = await resend.Emails.send_async(params)
        except (ResendError, NoContentError) as exc:
            # ResendError covers its own 4xx/5xx mapping *and* transport failures
            # (wrapped as error_type="HttpClientError"); NoContentError is the one
            # failure the SDK raises outside that hierarchy.
            structured(
                logger,
                logging.WARNING,
                "email send failed",
                user_id=user_id,
                purpose=purpose,
                error=type(exc).__name__,
                error_type=getattr(exc, "error_type", None),
                provider_code=getattr(exc, "code", None),
            )
            raise ServiceUnavailableError(_USER_SAFE_MESSAGE) from exc

        structured(
            logger,
            logging.INFO,
            "email code sent",
            user_id=user_id,
            purpose=purpose,
            email_id=response.get("id") if isinstance(response, dict) else None,
        )
