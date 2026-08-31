"""Time helpers.

The Oracle schema stores ``TIMESTAMP`` (no time zone). The application treats all
timestamps as UTC and strips the timezone before persisting, so every ``datetime``
read from the database is a naive UTC value.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as a naive UTC datetime (Oracle TIMESTAMP compatible)."""
    return datetime.now(UTC).replace(tzinfo=None)
