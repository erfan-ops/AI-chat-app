"""Enrolling an authenticator, and signing in with it, through the API.

The three requirements these pin down: a secret is stored but nothing is enabled until
a code from the app comes back; every code is checked against local time plus the
*cached* NTP offset, so verification never performs a network round trip; and the
channels that were there before — SMS and email — still work unchanged.

Codes are produced by the same library the server uses, on the fake clock the tests
control, so nothing here depends on the wall clock or on the internet.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.dependencies as dependencies
from app.db.models.user import User
from app.main import app
from app.services.time_service import TimeService
from app.services.totp_service import TotpService
from tests.conftest import (
    TEST_SETTINGS,
    FakeClock,
    FakeNtpClient,
    FakeTimeService,
    RecordingEmailService,
    RecordingSmsService,
    auth_user,
    login_user,
)

MOBILE = "9123456789"
EMAIL = "alice@example.com"


async def _enroll(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post("/me/totp/enable", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _code(totp: TotpService, secret: str, time_service: FakeTimeService, *, drift: int = 0) -> str:
    """The code the user's app would be showing — computed the way the app would."""
    return totp.code_at(secret=secret, unix_time=time_service.get_current_unix_time() + drift)


async def _confirm(
    client: AsyncClient, headers: dict[str, str], challenge: str, code: str
) -> Response:
    return await client.post(
        "/me/otp/verify", json={"challenge_id": challenge, "code": code}, headers=headers
    )


async def _stored(session_factory: async_sessionmaker[AsyncSession], user_id: int) -> User:
    async with session_factory() as db:
        user = (await db.scalars(select(User).where(User.id == user_id))).first()
        assert user is not None
        return user


async def _enable_sms(
    client: AsyncClient,
    headers: dict[str, str],
    sms: RecordingSmsService,
    clock: FakeClock,
    mobile: str = MOBILE,
) -> None:
    """Enable two-step verification by SMS.

    Leaves the clock past the resend cooldown: the enable send spends the same
    per-user budget a later login code would need.
    """
    start = await client.post("/me/otp/enable", json={"mobile_number": mobile}, headers=headers)
    assert start.status_code == 200, start.text
    verified = await _confirm(client, headers, start.json()["challenge_id"], sms.last_code)
    assert verified.status_code == 200, verified.text
    clock.advance(61)


async def _enable_email(
    client: AsyncClient, headers: dict[str, str], email: RecordingEmailService, clock: FakeClock
) -> None:
    start = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
    )
    assert start.status_code == 200, start.text
    verified = await _confirm(client, headers, start.json()["challenge_id"], email.last_code)
    assert verified.status_code == 200, verified.text
    clock.advance(61)


async def _enroll_and_confirm(
    client: AsyncClient,
    headers: dict[str, str],
    totp: TotpService,
    time_service: FakeTimeService,
) -> str:
    """Run the whole enrolment and return the secret that is now enrolled."""
    enrollment = await _enroll(client, headers)
    confirmed = await _confirm(
        client,
        headers,
        enrollment["challenge_id"],
        _code(totp, enrollment["secret"], time_service),
    )
    assert confirmed.status_code == 200, confirmed.text
    return str(enrollment["secret"])


# -- Enrolment ------------------------------------------------------------------------


async def test_enrolling_stores_a_secret_without_enabling_anything(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    totp_service: TotpService,
) -> None:
    headers, user = await auth_user(client)

    enrolled = await _enroll(client, headers)

    assert set(enrolled) == {"challenge_id", "secret", "otpauth_uri", "code_expires_in_seconds"}
    assert enrolled["otpauth_uri"].startswith("otpauth://totp/")
    # The secret is remembered, so the code that comes back can be checked against it.
    assert (await _stored(session_factory, user["id"])).totp_secret == enrolled["secret"]
    # But a secret is not a second factor: holding one proves nothing by itself.
    profile = await client.get("/me", headers=headers)
    assert profile.json()["otp_enabled"] is False
    # Which means the password alone is still enough to sign in.
    assert "access_token" in await login_user(client, "alice")


async def test_the_profile_never_carries_the_secret(
    client: AsyncClient, totp_service: TotpService
) -> None:
    headers, _user = await auth_user(client)
    enrolled = await _enroll(client, headers)

    profile = await client.get("/me", headers=headers)

    assert profile.status_code == 200
    assert "totp_secret" not in profile.json()
    assert enrolled["secret"] not in profile.text
    assert enrolled["otpauth_uri"] not in profile.text
    # The client is told the method is available, and nothing more.
    assert profile.json()["authenticator_enrolled"] is True


async def test_the_profile_reports_no_authenticator_before_one_is_enrolled(
    client: AsyncClient,
) -> None:
    headers, _user = await auth_user(client)

    assert (await client.get("/me", headers=headers)).json()["authenticator_enrolled"] is False


async def test_enrolling_again_replaces_the_secret(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    totp_service: TotpService,
) -> None:
    """Re-enrolling must invalidate the old app entry, not leave two working ones."""
    headers, user = await auth_user(client)
    first = await _enroll(client, headers)

    second = await _enroll(client, headers)

    assert second["secret"] != first["secret"]
    assert (await _stored(session_factory, user["id"])).totp_secret == second["secret"]


async def test_a_wrong_code_leaves_two_step_verification_off(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    totp_service: TotpService,
    time_service: FakeTimeService,
) -> None:
    headers, user = await auth_user(client)
    enrolled = await _enroll(client, headers)
    current = _code(totp_service, enrolled["secret"], time_service)
    mistyped = f"{current[0]}{(int(current[1]) + 1) % 10}{current[2:]}"

    rejected = await _confirm(client, headers, enrolled["challenge_id"], mistyped)

    assert rejected.status_code == 400
    stored = await _stored(session_factory, user["id"])
    assert stored.otp_enabled == 0
    assert stored.preferred_otp_method is None
    # The code the app is showing is still good: only the attempt was spent.
    assert (await _confirm(client, headers, enrolled["challenge_id"], current)).status_code == 200


async def test_a_valid_code_enables_the_authenticator_as_the_default(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    totp_service: TotpService,
    time_service: FakeTimeService,
) -> None:
    headers, user = await auth_user(client)
    enrolled = await _enroll(client, headers)

    confirmed = await _confirm(
        client,
        headers,
        enrolled["challenge_id"],
        _code(totp_service, enrolled["secret"], time_service),
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["otp_enabled"] is True
    # First time on: the method that was just verified is where codes must go next.
    assert confirmed.json()["preferred_otp_method"] == "TOTP"
    stored = await _stored(session_factory, user["id"])
    assert (stored.otp_enabled, stored.preferred_otp_method) == (1, "TOTP")


async def test_an_expired_enrolment_challenge_is_refused(
    client: AsyncClient,
    totp_service: TotpService,
    time_service: FakeTimeService,
    clock: FakeClock,
) -> None:
    """The enrolment window closes, and a code from a stale challenge is not a proof."""
    headers, _user = await auth_user(client)
    enrolled = await _enroll(client, headers)
    code = _code(totp_service, enrolled["secret"], time_service)

    clock.advance(TEST_SETTINGS.totp_enroll_ttl_seconds + 1)

    rejected = await _confirm(client, headers, enrolled["challenge_id"], code)
    assert rejected.status_code == 400
    assert "expired" in rejected.json()["detail"]


async def test_enrolling_does_not_move_a_default_that_already_exists(
    client: AsyncClient,
    sms: RecordingSmsService,
    clock: FakeClock,
    totp_service: TotpService,
    time_service: FakeTimeService,
) -> None:
    """An SMS account that adds an authenticator keeps sending login codes by SMS.

    This is the TOTP form of the rule that adding a second contact must not quietly
    change where the user's codes go.
    """
    headers, _user = await auth_user(client)
    await _enable_sms(client, headers, sms, clock)

    await _enroll_and_confirm(client, headers, totp_service, time_service)

    assert (await client.get("/me", headers=headers)).json()["preferred_otp_method"] == "SMS"
    # And the next login still goes to the phone.
    login = await login_user(client, "alice")
    assert login["otp_required"] is True
    assert login["delivery_method"] == "SMS"
    assert len(sms.sent) == 2  # the enable code, then this login's


# -- Signing in -----------------------------------------------------------------------


async def test_signing_in_requires_the_authenticator_code(
    client: AsyncClient,
    sms: RecordingSmsService,
    email: RecordingEmailService,
    totp_service: TotpService,
    time_service: FakeTimeService,
) -> None:
    headers, _user = await auth_user(client)
    secret = await _enroll_and_confirm(client, headers, totp_service, time_service)
    sms.sent.clear()
    email.sent.clear()

    login = await login_user(client, "alice")

    # No token, and nothing sent anywhere: the user chose their own app.
    assert login["otp_required"] is True
    assert login["delivery_method"] == "TOTP"
    assert sms.sent == []
    assert email.sent == []

    completed = await client.post(
        "/auth/login/otp",
        json={
            "challenge_id": login["challenge_id"],
            "code": _code(totp_service, secret, time_service),
        },
    )

    assert completed.status_code == 200, completed.text
    assert completed.json()["user"]["username"] == "alice"
    assert completed.json()["access_token"]
    # Still nothing sent: an authenticator login never touches a provider.
    assert sms.sent == [] and email.sent == []


async def test_a_wrong_authenticator_code_is_a_400_not_a_401(
    client: AsyncClient, totp_service: TotpService, time_service: FakeTimeService
) -> None:
    """400, never 401 — the client signs out of an unrelated session on a 401."""
    headers, _user = await auth_user(client)
    secret = await _enroll_and_confirm(client, headers, totp_service, time_service)
    login = await login_user(client, "alice")
    current = _code(totp_service, secret, time_service)
    mistyped = f"{current[0]}{(int(current[1]) + 1) % 10}{current[2:]}"

    response = await client.post(
        "/auth/login/otp", json={"challenge_id": login["challenge_id"], "code": mistyped}
    )

    assert response.status_code == 400
    assert "access_token" not in response.text


async def test_a_code_from_the_neighbouring_period_signs_in(
    client: AsyncClient,
    clock: FakeClock,
    totp_service: TotpService,
    time_service: FakeTimeService,
) -> None:
    """The window is real: the phone's clock may be half a period off the server's."""
    headers, _user = await auth_user(client)
    secret = await _enroll_and_confirm(client, headers, totp_service, time_service)

    for drift in (-30, 30):
        clock.advance(61)  # a second login needs a new challenge, not a new code
        login = await login_user(client, "alice")
        response = await client.post(
            "/auth/login/otp",
            json={
                "challenge_id": login["challenge_id"],
                "code": _code(totp_service, secret, time_service, drift=drift),
            },
        )
        assert response.status_code == 200, (drift, response.text)


async def test_signing_in_can_switch_to_the_authenticator(
    client: AsyncClient,
    sms: RecordingSmsService,
    clock: FakeClock,
    totp_service: TotpService,
    time_service: FakeTimeService,
) -> None:
    """A code we send and a code the app makes are two channels for one login."""
    headers, _user = await auth_user(client)
    await _enable_sms(client, headers, sms, clock)
    secret = await _enroll_and_confirm(client, headers, totp_service, time_service)
    login = await login_user(client, "alice")
    assert login["delivery_method"] == "SMS"
    assert login["alternative_method"] == "TOTP"
    sent_before = len(sms.sent)

    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": login["challenge_id"], "method": "TOTP"},
    )

    assert switched.status_code == 200, switched.text
    assert switched.json()["delivery_method"] == "TOTP"
    assert len(sms.sent) == sent_before  # switching to the app sends nothing
    completed = await client.post(
        "/auth/login/otp",
        json={
            "challenge_id": switched.json()["challenge_id"],
            "code": _code(totp_service, secret, time_service),
        },
    )
    assert completed.status_code == 200, completed.text
    # The saved default is untouched: this was a choice for this login only.
    assert (await client.get("/me", headers=headers)).json()["preferred_otp_method"] == "SMS"


async def test_switching_to_an_authenticator_that_is_not_enrolled_fails_closed(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await auth_user(client)
    await _enable_sms(client, headers, sms, clock)
    login = await login_user(client, "alice")

    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": login["challenge_id"], "method": "TOTP"},
    )

    assert switched.status_code == 503
    # The code the user already has keeps working.
    assert (
        await client.post(
            "/auth/login/otp",
            json={"challenge_id": login["challenge_id"], "code": sms.last_code},
        )
    ).status_code == 200


async def test_signing_in_refuses_when_the_clock_is_not_trustworthy(
    client: AsyncClient, totp_service: TotpService, time_service: FakeTimeService
) -> None:
    headers, _user = await auth_user(client)
    secret = await _enroll_and_confirm(client, headers, totp_service, time_service)
    login = await login_user(client, "alice")

    time_service.usable = False  # the offset went stale; nobody has confirmed the time
    response = await client.post(
        "/auth/login/otp",
        json={
            "challenge_id": login["challenge_id"],
            "code": _code(totp_service, secret, time_service),
        },
    )

    assert response.status_code == 503
    assert "access_token" not in response.text


# -- The other two channels still work -------------------------------------------------


async def test_sms_two_step_still_works(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await auth_user(client)
    await _enable_sms(client, headers, sms, clock)

    login = await login_user(client, "alice")

    assert login["delivery_method"] == "SMS"
    completed = await client.post(
        "/auth/login/otp", json={"challenge_id": login["challenge_id"], "code": sms.last_code}
    )
    assert completed.status_code == 200
    assert completed.json()["access_token"]


async def test_email_two_step_still_works(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    headers, _user = await auth_user(client)
    await _enable_email(client, headers, email, clock)

    login = await login_user(client, "alice")

    assert login["delivery_method"] == "EMAIL"
    completed = await client.post(
        "/auth/login/otp", json={"challenge_id": login["challenge_id"], "code": email.last_code}
    )
    assert completed.status_code == 200
    assert completed.json()["access_token"]


# -- The clock, end to end -------------------------------------------------------------


async def test_verification_uses_the_cached_offset_and_never_calls_ntp(
    client: AsyncClient, time_service: FakeTimeService
) -> None:
    """The headline requirement, with the real ``TimeService`` in the request path.

    A login that verifies an authenticator code must be local arithmetic on an offset
    measured earlier — exactly one NTP round trip for the whole test, and it is the
    one the test asks for.
    """
    ntp = FakeNtpClient(offset=42.0)  # the server's clock is 42s behind the true time
    real_clock = TimeService(TEST_SETTINGS, client=ntp)
    app.dependency_overrides[dependencies.get_time_service] = lambda: real_clock
    app.dependency_overrides[dependencies.get_totp_service] = lambda: TotpService(real_clock)

    assert await real_clock.synchronize() is True
    assert ntp.calls == 1

    headers, _user = await auth_user(client)
    enrolled = await _enroll(client, headers)
    code = TotpService(real_clock).code_at(
        secret=enrolled["secret"], unix_time=real_clock.get_current_unix_time()
    )
    confirmed = await _confirm(client, headers, enrolled["challenge_id"], code)
    assert confirmed.status_code == 200, confirmed.text

    login = await login_user(client, "alice")
    completed = await client.post(
        "/auth/login/otp",
        json={"challenge_id": login["challenge_id"], "code": code},
    )

    assert completed.status_code == 200, completed.text
    # One synchronization for the whole enrolment and login: the corrected time came
    # from the cached offset both times.
    assert ntp.calls == 1
    assert ntp.requests[0]["host"] == TEST_SETTINGS.ntp_server
