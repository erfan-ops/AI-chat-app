"""The application's clock: the local system time plus a measured NTP offset.

A TOTP code is a function of the current time, so a server whose clock has drifted
verifies codes nobody's phone will produce. This service keeps a *corrected* clock:
it asks an NTP server for the time occasionally, stores the **offset** between that
server and the local clock, and answers every later question from local time plus
that offset.

Two rules shape it:

- **No network on the hot path.** ``get_current_unix_time()`` is arithmetic. A
  synchronization is a deliberate, periodic act (see ``run``), never something a
  login triggers.
- **A failure is not a silent success.** The last good offset is kept when a sync
  fails, and if it gets older than ``ntp_max_offset_age_seconds`` the clock is
  reported unusable so TOTP verification refuses (503) instead of trusting a clock
  nobody has confirmed.

The offset is runtime state and lives in memory only: it is a property of this
process's clock, not of the data, and it is learned again within an interval.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, ClassVar, Protocol

import ntplib

from app.core.config import Settings
from app.core.logging import get_logger, structured

logger = get_logger("app.services.time")


class NtpClient(Protocol):
    """Just enough of ``ntplib.NTPClient`` to swap it out in tests."""

    def request(self, host: str, version: int = 2, port: str = "ntp", timeout: float = 5) -> Any:
        """Return a response whose ``offset`` and ``delay`` are in seconds."""
        ...


class TimeService:
    """Corrected UTC time, from local time plus the last measured NTP offset."""

    _instance: ClassVar[TimeService | None] = None

    def __init__(
        self,
        settings: Settings,
        *,
        client: NtpClient | None = None,
        local_clock: Any = time.time,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._settings = settings
        self._client = client or ntplib.NTPClient()
        self._local_clock = local_clock
        # Age is measured on a monotonic clock: a wall-clock jump must not make a
        # stale offset look fresh.
        self._monotonic = monotonic
        self._offset: float | None = None
        self._synced_at: float | None = None

    # -- the one clock this process has ------------------------------------------

    @classmethod
    def instance(cls, settings: Settings) -> TimeService:
        """The process's clock, created on first use and then always the same object.

        Not a cache keyed on ``settings``. The offset is mutable state, so two
        instances would be two answers to "what time is it" — and only the instance
        that synchronized would give the right one. A keyed cache can hand out a
        second instance for the same configuration (a positional call and a keyword
        call are different keys), which is exactly the failure this avoids: the
        synchronization loop and the request that verifies a code must share a clock.
        """
        if cls._instance is None:
            cls._instance = cls(settings)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Forget the instance (tests)."""
        cls._instance = None

    # -- state ---------------------------------------------------------------------

    @property
    def synchronized(self) -> bool:
        """Whether an offset was ever measured (it may still be stale)."""
        return self._offset is not None

    @property
    def offset(self) -> float | None:
        """Seconds the local clock is behind (positive) or ahead of NTP."""
        return self._offset

    @property
    def offset_age_seconds(self) -> float | None:
        return None if self._synced_at is None else self._monotonic() - self._synced_at

    @property
    def is_usable(self) -> bool:
        """A synchronized offset that is still young enough to trust."""
        age = self.offset_age_seconds
        return age is not None and age <= self._settings.ntp_max_offset_age_seconds

    @property
    def status(self) -> str:
        """``synchronized`` | ``stale`` | ``never_synchronized`` — for logs and /health."""
        if not self.synchronized:
            return "never_synchronized"
        return "synchronized" if self.is_usable else "stale"

    def get_current_unix_time(self) -> float:
        """Corrected Unix time. Never touches the network."""
        return self._local_clock() + (self._offset or 0.0)

    # -- synchronization -----------------------------------------------------------

    async def synchronize(self) -> bool:
        """Measure the offset once. Returns whether the offset is now fresh.

        The round trip runs in a thread because the NTP client is blocking; the
        offset itself comes from the protocol's own calculation, which cancels the
        request/response delay (``ntplib`` implements
        ``((recv - orig) + (tx - dest)) / 2``).
        """
        settings = self._settings
        try:
            sample = await asyncio.to_thread(
                self._client.request,
                settings.ntp_server,
                version=2,
                port="ntp",
                timeout=settings.ntp_timeout_seconds,
            )
        except (ntplib.NTPException, OSError) as exc:
            # OSError covers socket timeouts and DNS failures. Nothing about the
            # request is logged — a sync carries no secrets.
            structured(
                logger,
                logging.WARNING,
                "ntp synchronization failed",
                server=settings.ntp_server,
                error=type(exc).__name__,
                status=self.status,
            )
            return False

        delay = float(getattr(sample, "delay", 0.0))
        if delay > settings.ntp_max_delay_seconds:
            # A slow round trip means the offset is only accurate to about half of it.
            structured(
                logger,
                logging.WARNING,
                "ntp sample rejected",
                server=settings.ntp_server,
                delay_seconds=round(delay, 3),
                max_delay_seconds=settings.ntp_max_delay_seconds,
            )
            return False

        self._offset = float(sample.offset)
        self._synced_at = self._monotonic()
        structured(
            logger,
            logging.INFO,
            "ntp synchronized",
            server=settings.ntp_server,
            offset_seconds=round(self._offset, 3),
            delay_seconds=round(delay, 3),
        )
        return True

    async def run(self, stop: asyncio.Event) -> None:
        """Synchronize now, then keep refreshing until ``stop`` is set.

        A failed sync retries sooner than the regular interval, so an unreachable
        server is picked up again promptly without being hammered.
        """
        while not stop.is_set():
            synced = await self.synchronize()
            delay = (
                self._settings.ntp_sync_interval_seconds
                if synced
                else self._settings.ntp_retry_interval_seconds
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                continue
