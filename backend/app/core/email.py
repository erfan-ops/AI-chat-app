"""Email-address normalization for the email OTP channel.

The database column is ``USERS.EMAIL VARCHAR2(200)`` with **byte** semantics, so the
address is stored in its canonical lowercase ASCII form: a longer internationalized
address (or one typed with Persian characters) would otherwise count differently in
bytes than in characters and could break the bind — the same reason
``app/core/mobile.py`` insists on ASCII digits.
"""

from __future__ import annotations

from app.exceptions import BadRequestError

# Matches the Oracle column exactly.
MAX_EMAIL_LENGTH = 200


def normalize_email(raw: str) -> str:
    """Return the canonical lowercase address, or raise ``BadRequestError``.

    Deliberately not a full RFC 5322 parser: it checks the shape that matters for
    delivery (one ``@``, a dot-separated domain, no whitespace, ASCII only) and
    lowercases the whole address, so comparisons against the stored value behave
    the same on Oracle (case-sensitive) as they do in Python.
    """
    value = (raw or "").strip()
    if not value:
        raise BadRequestError("Enter your email address")
    if not value.isascii():
        raise BadRequestError("Use English letters and digits for your email address")
    if len(value) > MAX_EMAIL_LENGTH:
        raise BadRequestError(f"Email addresses can be at most {MAX_EMAIL_LENGTH} characters")
    if value.count("@") != 1:
        raise BadRequestError("Enter a valid email address")
    if any(character.isspace() for character in value):
        raise BadRequestError("Enter a valid email address")

    local, _, domain = value.partition("@")
    if not local or not domain:
        raise BadRequestError("Enter a valid email address")
    # Printable ASCII only. ``isspace()`` above lets a NUL, BEL or DEL through, and
    # such a value would be stored and echoed back by /me before the provider ever
    # rejected it — the same "keep it to the bytes the column can hold" rule the
    # mobile number follows.
    if any(not (" " < character < "\x7f") for character in value):
        raise BadRequestError("Use English letters and digits for your email address")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise BadRequestError("Enter a valid email address")
    if (
        "." not in domain
        or ".." in domain
        or domain.startswith((".", "-"))
        or domain.endswith((".", "-"))
    ):
        raise BadRequestError("Enter a valid email address")

    return value.lower()


def mask_email(email: str) -> str:
    """``ali@gmail.com`` → ``al***@gmail.com``.

    The domain stays readable (it says which provider to check, and the caller
    already knows it); the local part — the part that identifies the person — is
    not.
    """
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}"
