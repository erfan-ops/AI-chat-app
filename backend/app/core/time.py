"""Time helpers.

The Oracle schema stores ``TIMESTAMP`` (no time zone). The application treats all
timestamps as UTC and strips the timezone before persisting, so every ``datetime``
read from the database is a naive UTC value.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone


def utcnow() -> datetime:
    """Return the current time as a naive UTC datetime (Oracle TIMESTAMP compatible)."""
    return datetime.now(UTC).replace(tzinfo=None)


def local_time(offset_minutes: int) -> datetime:
    """The wall-clock time at a UTC offset, carrying that offset.

    ``datetime.now(UTC)`` shifted by the offset is the same instant read on the
    user's clock, and the attached ``timezone`` makes ``isoformat()`` render it as
    ``+03:30`` — the form the model can reason with.
    """
    offset = timedelta(minutes=offset_minutes)
    return (datetime.now(UTC) + offset).replace(tzinfo=timezone(offset))


def unix_timestamp() -> int:
    """Current time as whole seconds since the epoch (Cloudinary request signing).

    Uses an *aware* datetime on purpose: ``.timestamp()`` on a naive datetime is
    interpreted as local time, which would send a timestamp hours in the past and
    get the upload rejected as stale.
    """
    return int(datetime.now(UTC).timestamp())
