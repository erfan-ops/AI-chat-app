"""The corrected clock: measuring an NTP offset, and everything that must not.

``TimeService`` exists so that nothing which verifies a code ever performs a network
round trip. The NTP client here is the recording fake from ``conftest``: the suite
never contacts ``ntp.time.ir`` (or anything else), and no test needs internet access.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import ntplib
import pytest

from app.services.time_service import TimeService
from tests.conftest import TEST_SETTINGS, FakeNtpClient

# Two settings sets that make the loop's choice of interval *observable*: each one is
# far shorter than the other, so "how many calls did this produce in a moment?" tells
# which of the two delays the loop actually waited on. ``model_copy`` skips
# validation, which is what lets them sit below the production minimum.
_SOON, _NEVER = 0.005, 10.0
SYNC_FAST = TEST_SETTINGS.model_copy(
    update={"ntp_sync_interval_seconds": _SOON, "ntp_retry_interval_seconds": _NEVER}
)
RETRY_FAST = TEST_SETTINGS.model_copy(
    update={"ntp_sync_interval_seconds": _NEVER, "ntp_retry_interval_seconds": _SOON}
)


class ManualClock:
    """A hand-cranked stand-in for ``time.time`` / ``time.monotonic``."""

    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build(
    *,
    offset: float = 0.0,
    delay: float = 0.01,
    error: Exception | None = None,
    settings: Any = TEST_SETTINGS,
) -> tuple[TimeService, FakeNtpClient, ManualClock, ManualClock]:
    client = FakeNtpClient(offset=offset, delay=delay, error=error)
    local, monotonic = ManualClock(1_750_000_000.0), ManualClock(100.0)
    service = TimeService(settings, client=client, local_clock=local, monotonic=monotonic)
    return service, client, local, monotonic


async def _until(predicate: Callable[[], bool], *, limit: float = 2.0) -> None:
    """Spin the event loop until ``predicate`` holds — for the sync loop's real task."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("the condition was not met in time")
        await asyncio.sleep(0.001)


async def _run_briefly(service: TimeService) -> None:
    """Let the synchronization loop run for a moment, then stop it cleanly."""
    stop = asyncio.Event()
    task = asyncio.create_task(service.run(stop))
    try:
        await asyncio.sleep(0.3)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)


# -- Before any synchronization -------------------------------------------------------


async def test_a_fresh_clock_is_never_synchronized() -> None:
    service, client, _local, _monotonic = build()

    assert service.synchronized is False
    assert service.status == "never_synchronized"
    assert service.offset is None
    assert service.is_usable is False
    # Local time is still reported — it is arithmetic, and *usability* is what the
    # callers check before trusting a code against it.
    assert service.get_current_unix_time() == pytest.approx(1_750_000_000.0)
    assert client.calls == 0


async def test_the_default_client_is_a_real_ntp_client() -> None:
    """NTP is spoken over UDP by a protocol library — never over HTTP."""
    assert isinstance(TimeService(TEST_SETTINGS)._client, ntplib.NTPClient)


def test_the_whole_process_shares_one_clock() -> None:
    """The clock that gets synchronized must be the clock that verifies a code.

    This is a regression test with a specific shape: the dependency used to be
    ``lru_cache``d on the settings object, and a positional call and a keyword call
    are *different cache keys*, so the synchronization loop filled one instance while
    FastAPI's dependency injection handed every request a second, never-synchronized
    one. Verification then refused every authenticator code with a 503.
    """
    try:
        from app.api.dependencies import get_time_service

        service = TimeService.instance(TEST_SETTINGS)
        assert TimeService.instance(TEST_SETTINGS) is service
        # Both call shapes FastAPI and the lifespan can produce.
        assert get_time_service(TEST_SETTINGS) is service
        assert get_time_service(settings=TEST_SETTINGS) is service
    finally:
        TimeService.reset_instance()


def test_a_clock_that_was_never_synchronized_is_not_usable() -> None:
    """The state the shared instance must never be read in without a sync."""
    service = TimeService(TEST_SETTINGS, client=FakeNtpClient())
    assert service.is_usable is False


# -- Synchronizing --------------------------------------------------------------------


async def test_synchronize_measures_the_offset_from_the_configured_server() -> None:
    service, client, _local, _monotonic = build(offset=2.5)

    assert await service.synchronize() is True

    assert client.calls == 1
    assert client.requests[0]["host"] == TEST_SETTINGS.ntp_server
    assert client.requests[0]["timeout"] == TEST_SETTINGS.ntp_timeout_seconds
    assert service.offset == pytest.approx(2.5)
    assert service.synchronized is True
    assert service.status == "synchronized"
    assert service.is_usable is True
    assert service.offset_age_seconds == pytest.approx(0.0)


async def test_the_corrected_time_is_local_time_plus_the_offset() -> None:
    service, _client, local, _monotonic = build(offset=-12.0)
    await service.synchronize()

    local.advance(30)

    assert service.get_current_unix_time() == pytest.approx(1_750_000_030.0 - 12.0)


async def test_reading_the_time_never_touches_the_network() -> None:
    """The whole point: verification is arithmetic on a cached offset."""
    service, client, local, _monotonic = build(offset=1.0)
    await service.synchronize()

    for _ in range(100):
        local.advance(1)
        service.get_current_unix_time()

    assert client.calls == 1


async def test_a_sample_slower_than_the_limit_is_rejected() -> None:
    """A slow round trip bounds the sample's accuracy, so it is not trusted."""
    service, client, _local, _monotonic = build(delay=TEST_SETTINGS.ntp_max_delay_seconds + 1.0)

    assert await service.synchronize() is False

    assert client.calls == 1
    assert service.offset is None
    assert service.status == "never_synchronized"
    assert service.is_usable is False


# -- Failing --------------------------------------------------------------------------


async def test_an_ntp_error_is_reported_as_a_failure_not_an_exception() -> None:
    service, client, _local, _monotonic = build(error=ntplib.NTPException("no route to host"))

    assert await service.synchronize() is False

    assert client.calls == 1
    assert service.is_usable is False


async def test_a_socket_failure_is_handled_the_same_way() -> None:
    """Timeouts and DNS failures arrive as ``OSError``, not as NTP errors."""
    service, _client, _local, _monotonic = build(error=OSError("timed out"))

    assert await service.synchronize() is False
    assert service.status == "never_synchronized"


async def test_a_failed_sync_keeps_the_last_valid_offset() -> None:
    service, client, _local, _monotonic = build(offset=2.5)
    await service.synchronize()

    client.error = ntplib.NTPException("server unreachable")

    assert await service.synchronize() is False
    assert service.offset == pytest.approx(2.5)
    assert service.status == "synchronized"


async def test_a_rejected_sample_never_replaces_a_good_offset() -> None:
    service, client, _local, _monotonic = build(offset=2.5)
    await service.synchronize()

    client.offset, client.delay = 99.0, TEST_SETTINGS.ntp_max_delay_seconds + 5.0

    assert await service.synchronize() is False
    assert service.offset == pytest.approx(2.5)


async def test_an_offset_that_stops_being_refreshed_goes_stale() -> None:
    """A clock nobody has confirmed for too long must stop being trusted.

    This is what keeps verification from quietly riding an offset measured hours ago:
    the offset is still remembered, but ``is_usable`` (which TOTP checks) says no.
    """
    service, _client, _local, monotonic = build(offset=1.0)
    await service.synchronize()

    monotonic.advance(TEST_SETTINGS.ntp_max_offset_age_seconds + 1)

    assert service.synchronized is True
    assert service.status == "stale"
    assert service.is_usable is False


async def test_offset_age_is_measured_on_the_monotonic_clock() -> None:
    """A wall-clock jump (NTP stepping it, an admin editing it) means nothing here."""
    service, _client, local, monotonic = build(offset=1.0)
    await service.synchronize()

    local.advance(86_400)  # a day of local-clock drift
    monotonic.advance(5.0)  # but only five seconds of real elapsed time

    assert service.offset_age_seconds == pytest.approx(5.0)
    assert service.is_usable is True


# -- The synchronization loop ---------------------------------------------------------


async def test_the_loop_synchronizes_at_startup() -> None:
    service, client, _local, _monotonic = build(offset=3.0, settings=SYNC_FAST)

    await _run_briefly(service)

    assert client.calls >= 1
    assert service.offset == pytest.approx(3.0)
    assert service.is_usable is True


async def test_the_loop_keeps_refreshing_after_a_successful_sync() -> None:
    service, client, _local, _monotonic = build(offset=3.0, settings=SYNC_FAST)

    await _run_briefly(service)

    # Several round trips in a fraction of a second: only the sync interval is short.
    assert client.calls >= 5


async def test_the_loop_retries_a_failure_sooner_than_the_regular_interval() -> None:
    service, client, _local, _monotonic = build(
        error=ntplib.NTPException("server unreachable"), settings=RETRY_FAST
    )

    await _run_briefly(service)

    assert client.calls >= 5
    assert service.status == "never_synchronized"
    assert service.is_usable is False
