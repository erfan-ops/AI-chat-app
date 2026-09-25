"""The limits on asking for codes, and on guessing at them.

They overlap on purpose — each one stops something the others cannot:

- a per-(user, method) **resend cooldown** (60 s), so a live challenge cannot be
  replaced per guess;
- a per-user **daily cap** (10), which is about messages and their cost;
- a per-**destination** window (3 per 15 min), because an address or number is
  reachable whichever account asks for the code — throwaway accounts are free;
- a per-**client address** window (10 per 15 min), the same argument for a network;
- a per-challenge **attempt cap** (5), and, for authenticator codes, a five-minute
  **lock** once that many wrong codes have been tried.

The unit tests drive ``OtpService`` directly on the deterministic clock, so a window
that takes fifteen minutes to open is exercised without waiting for one.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.api.dependencies import get_sms_service
from app.exceptions import BadRequestError, RateLimitError
from app.main import app
from app.services.otp_service import OtpService
from app.services.totp_service import TotpService
from tests.conftest import (
    TEST_SETTINGS,
    FakeClock,
    FakeTimeService,
    RecordingEmailService,
    RecordingSmsService,
    auth_user,
    login_user,
)

SHARED_EMAIL = "shared@example.com"
OTHER_EMAIL = "other@example.com"
MOBILE = "9123456789"
IP = "203.0.113.7"
OTHER_IP = "198.51.100.9"

WINDOW = TEST_SETTINGS.otp_rate_window_seconds
COOLDOWN = TEST_SETTINGS.otp_resend_cooldown_seconds
# The refusals round up, so a lock of 300 s answers with 301.
LOCKOUT = TEST_SETTINGS.totp_lockout_seconds + 1


def build(clock: FakeClock, **limits: Any) -> OtpService:
    """A service with its own store, and optionally its own limits."""
    settings = TEST_SETTINGS.model_copy(update=limits) if limits else TEST_SETTINGS
    return OtpService(settings, clock=clock)


def send(
    service: OtpService,
    *,
    user_id: int,
    destination: str,
    client_ip: str | None = IP,
    method: str = "EMAIL",
) -> str:
    challenge_id, _code = service.claim(
        user_id=user_id,
        purpose="login",
        method=method,
        destination=destination,
        client_ip=client_ip,
    )
    return challenge_id


def open_totp_challenge(service: OtpService, clock: FakeClock, *, user_id: int = 1) -> str:
    """A live authenticator challenge, with the resend cooldown behind it."""
    clock.advance(COOLDOWN + 1)
    challenge_id = service.claim_totp(user_id=user_id, purpose="login")
    service.commit(challenge_id)
    return challenge_id


def wrong_code(service: OtpService, challenge_id: str) -> None:
    """Spend one attempt on a code that is not it."""
    with pytest.raises(BadRequestError):
        service.verify(
            challenge_id=challenge_id,
            code="000000",
            purpose="login",
            validator=lambda _code: False,
        )


# -- Per destination -------------------------------------------------------------------


def test_a_fourth_code_to_one_destination_is_refused(clock: FakeClock) -> None:
    service = build(clock)

    for user_id in (1, 2, 3):
        send(service, user_id=user_id, destination=SHARED_EMAIL)

    with pytest.raises(RateLimitError) as refused:
        send(service, user_id=4, destination=SHARED_EMAIL)

    assert "destination" in str(refused.value)
    # It frees up when the oldest send ages out, and not before.
    assert refused.value.retry_after_seconds == WINDOW + 1


def test_the_destination_window_is_shared_by_every_account(clock: FakeClock) -> None:
    """The reason it exists: one address, many accounts.

    A per-user cap cannot bound this — an attacker registers as many accounts as the
    address they want to flood, and each brings its own allowance.
    """
    service = build(clock)

    for user_id in range(1, 4):
        send(service, user_id=user_id, destination=SHARED_EMAIL)

    with pytest.raises(RateLimitError):
        send(service, user_id=99, destination=SHARED_EMAIL)
    # A different address is untouched by it.
    send(service, user_id=99, destination=OTHER_EMAIL)


def test_mobile_and_email_windows_are_separate(clock: FakeClock) -> None:
    """Keyed by method *and* destination, so neither method can spend the other's."""
    service = build(clock)

    for user_id in (1, 2, 3):
        send(service, user_id=user_id, destination=MOBILE, method="SMS")

    # The same number under the other method has its own window…
    send(service, user_id=4, destination=MOBILE, method="EMAIL")
    # …and so does another address (a different account, since the one above has just
    # spent its own resend cooldown).
    send(service, user_id=5, destination=OTHER_EMAIL)


def test_the_destination_window_ages_out(clock: FakeClock) -> None:
    service = build(clock)
    for user_id in (1, 2, 3):
        send(service, user_id=user_id, destination=SHARED_EMAIL)
    with pytest.raises(RateLimitError):
        send(service, user_id=4, destination=SHARED_EMAIL)

    clock.advance(WINDOW + 1)

    send(service, user_id=4, destination=SHARED_EMAIL)


# -- Per client address ----------------------------------------------------------------


def test_an_address_gets_ten_requests_per_window(clock: FakeClock) -> None:
    service = build(clock)

    # Distinct users and destinations, so only the address window can bite.
    for index in range(TEST_SETTINGS.otp_max_sends_per_ip):
        send(service, user_id=index, destination=f"user{index}@example.com")

    with pytest.raises(RateLimitError) as refused:
        send(service, user_id=100, destination="last@example.com")

    assert "network" in str(refused.value)
    # Another address is unaffected by it.
    send(service, user_id=101, destination="elsewhere@example.com", client_ip=OTHER_IP)


def test_a_caller_without_an_address_is_not_counted(clock: FakeClock) -> None:
    """The transport always supplies an address; a direct service call need not.

    A window that cannot be fed must not be the thing that refuses a claim.
    """
    service = build(clock)

    for index in range(TEST_SETTINGS.otp_max_sends_per_ip + 5):
        send(service, user_id=index, destination=f"user{index}@example.com", client_ip=None)


def test_a_refused_claim_does_not_spend_a_window(clock: FakeClock) -> None:
    """Counters move only for requests that pass every limit.

    If the attempt refused by the cooldown below had consumed a destination slot, the
    third account here would already be over the limit — so this is what proves it did
    not.
    """
    service = build(clock)
    send(service, user_id=1, destination=SHARED_EMAIL, client_ip=IP)
    with pytest.raises(RateLimitError):
        send(service, user_id=1, destination=SHARED_EMAIL, client_ip=IP)  # cooldown

    clock.advance(COOLDOWN + 1)

    send(service, user_id=2, destination=SHARED_EMAIL, client_ip=IP)
    send(service, user_id=3, destination=SHARED_EMAIL, client_ip=IP)
    with pytest.raises(RateLimitError) as refused:
        send(service, user_id=4, destination=SHARED_EMAIL, client_ip=IP)
    assert "destination" in str(refused.value)


def test_the_daily_cap_still_applies_on_top_of_the_windows(clock: FakeClock) -> None:
    """The windows are additional, not a replacement for the per-user cap."""
    service = build(clock, otp_max_sends_per_destination=50, otp_max_sends_per_ip=50)

    for index in range(TEST_SETTINGS.otp_max_sends_per_day):
        send(service, user_id=1, destination=f"user{index}@example.com")
        clock.advance(COOLDOWN + 1)

    with pytest.raises(RateLimitError) as refused:
        send(service, user_id=1, destination="one-too-many@example.com")
    assert "today" in str(refused.value)


# -- The authenticator lock ------------------------------------------------------------


def test_five_wrong_authenticator_codes_lock_the_account(clock: FakeClock) -> None:
    service = build(clock)
    challenge_id = open_totp_challenge(service, clock)

    for _ in range(TEST_SETTINGS.otp_max_verify_attempts):
        wrong_code(service, challenge_id)

    # The lock is checked before the resend cooldown, and refuses the next challenge
    # outright: after this many wrong codes there is nothing to gain from a new one,
    # and saying so beats handing out a code that will not be checked.
    with pytest.raises(RateLimitError) as locked:
        service.claim_totp(user_id=1, purpose="login")

    assert "authenticator" in str(locked.value)
    assert locked.value.retry_after_seconds == LOCKOUT


def test_the_lock_is_enforced_on_the_attempt_too(clock: FakeClock) -> None:
    """Belt and braces — and with the failure count and the attempt cap both at five,
    the lock and the dead challenge coincide, so this is the only way the check is
    reached today. It is what makes the lock hold if either ever changes."""
    service = build(clock)
    challenge_id = open_totp_challenge(service, clock)
    service._totp_locks[1] = clock() + TEST_SETTINGS.totp_lockout_seconds

    with pytest.raises(RateLimitError):
        # That validator accepts the code: the refusal is the lock, not the guess.
        service.verify(
            challenge_id=challenge_id, code="123456", purpose="login", validator=lambda _: True
        )


def test_the_lock_lifts_after_its_minutes(clock: FakeClock) -> None:
    service = build(clock)
    challenge_id = open_totp_challenge(service, clock)
    for _ in range(TEST_SETTINGS.otp_max_verify_attempts):
        wrong_code(service, challenge_id)

    clock.advance(TEST_SETTINGS.totp_lockout_seconds + 1)

    open_totp_challenge(service, clock)


def test_a_correct_code_clears_the_failure_history(clock: FakeClock) -> None:
    """Proving possession of the authenticator starts the count again.

    A user who mistypes a few times and then gets it right is not left one slip away
    from a lockout.
    """
    service = build(clock)
    for _ in range(2):
        wrong_code(service, open_totp_challenge(service, clock))

    good = open_totp_challenge(service, clock)
    service.verify(challenge_id=good, code="123456", purpose="login", validator=lambda _: True)

    # Four more failures, but never a fifth in the window: the count restarted.
    for _ in range(4):
        wrong_code(service, open_totp_challenge(service, clock))
    open_totp_challenge(service, clock)


# -- Through the API -------------------------------------------------------------------


async def test_the_destination_window_survives_a_second_account(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    """Accounts cannot each spend a full allowance on one mobile number."""
    headers = {}
    for username in ("alice", "bob", "carol"):
        headers[username], _user = await auth_user(client, username)
        started = await client.post(
            "/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers[username]
        )
        assert started.status_code == 200, started.text
        clock.advance(COOLDOWN + 1)

    fourth = await client.post(
        "/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers["alice"]
    )

    assert fourth.status_code == 429
    assert "destination" in fourth.json()["detail"]
    assert fourth.headers["Retry-After"].isdigit()
    # The three codes that went out are still usable, and only three did.
    assert len(sms.sent) == 3


async def test_the_address_window_survives_a_second_account(
    client: AsyncClient, sms: RecordingSmsService
) -> None:
    """One network gets ten requests per window, however many accounts make them."""
    limit = TEST_SETTINGS.otp_max_sends_per_ip
    last = None
    for index in range(limit + 1):
        headers, _user = await auth_user(client, f"user{index}")
        last = await client.post(
            # A distinct number per account, so the destination window stays out of it.
            "/me/otp/enable",
            json={"mobile_number": f"9123456{index:03d}"},
            headers=headers,
        )

    assert last is not None and last.status_code == 429
    assert "network" in last.json()["detail"]
    # Not a 401: a rate limit must not sign an unrelated session out.
    assert "Retry-After" in last.headers


async def test_a_provider_without_a_key_is_refused_before_it_costs_anything(
    client: AsyncClient,
) -> None:
    """503 "not configured", not a rate limit, however many times it is tried.

    The provider is checked before the request is accounted for, so a deployment that
    cannot send SMS never fills the windows with messages that never left — which
    would answer "too many requests" to a user whose real problem is the missing key.
    """
    app.dependency_overrides.pop(get_sms_service, None)  # the real service, no key
    headers, _user = await auth_user(client)

    for _ in range(TEST_SETTINGS.otp_max_sends_per_destination + 3):
        response = await client.post(
            "/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers
        )
        assert response.status_code == 503, response.text
        assert response.json() == {"detail": "SMS is not configured"}


async def test_five_wrong_authenticator_codes_lock_that_account(
    client: AsyncClient,
    clock: FakeClock,
    totp_service: TotpService,
    time_service: FakeTimeService,
) -> None:
    headers, _user = await auth_user(client)
    enrolled = (await client.post("/me/totp/enable", headers=headers)).json()

    for _ in range(TEST_SETTINGS.otp_max_verify_attempts):
        refused = await client.post(
            "/me/otp/verify",
            json={"challenge_id": enrolled["challenge_id"], "code": "000000"},
            headers=headers,
        )
        assert refused.status_code == 400, refused.text

    locked = await client.post("/me/totp/enable", headers=headers)

    assert locked.status_code == 429
    assert "authenticator" in locked.json()["detail"]
    assert locked.headers["Retry-After"] == str(LOCKOUT)

    clock.advance(TEST_SETTINGS.totp_lockout_seconds + 1)
    assert (await client.post("/me/totp/enable", headers=headers)).status_code == 200


async def test_the_authenticator_lock_does_not_lock_the_account(
    client: AsyncClient,
    email: RecordingEmailService,
    clock: FakeClock,
    totp_service: TotpService,
    time_service: FakeTimeService,
) -> None:
    """A locked authenticator is not a locked account: the other methods still work.

    Someone whose phone is out of reach must not lose access because of a lock that
    was earned by somebody guessing.
    """
    headers, _user = await auth_user(client)
    started = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": SHARED_EMAIL}, headers=headers
    )
    assert started.status_code == 200, started.text
    assert (
        await client.post(
            "/me/otp/verify",
            json={"challenge_id": started.json()["challenge_id"], "code": email.last_code},
            headers=headers,
        )
    ).status_code == 200
    clock.advance(COOLDOWN + 1)
    enrolled = (await client.post("/me/totp/enable", headers=headers)).json()
    for _ in range(TEST_SETTINGS.otp_max_verify_attempts):
        await client.post(
            "/me/otp/verify",
            json={"challenge_id": enrolled["challenge_id"], "code": "000000"},
            headers=headers,
        )

    # Locked out of the authenticator...
    assert (await client.post("/me/totp/enable", headers=headers)).status_code == 429

    # ...but not out of the account: the saved default is still email, and it works.
    login = await login_user(client, "alice")
    assert login["otp_required"] is True
    assert login["delivery_method"] == "EMAIL"
    completed = await client.post(
        "/auth/login/otp",
        json={"challenge_id": login["challenge_id"], "code": email.last_code},
    )
    assert completed.status_code == 200, completed.text


async def test_switching_to_a_locked_authenticator_keeps_the_code_in_hand(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock, totp_service: TotpService
) -> None:
    """The switch fails closed, and nothing is sent by the attempt."""
    headers, _user = await auth_user(client)
    started = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)
    assert (
        await client.post(
            "/me/otp/verify",
            json={"challenge_id": started.json()["challenge_id"], "code": sms.last_code},
            headers=headers,
        )
    ).status_code == 200
    clock.advance(COOLDOWN + 1)
    enrolled = (await client.post("/me/totp/enable", headers=headers)).json()
    for _ in range(TEST_SETTINGS.otp_max_verify_attempts):
        await client.post(
            "/me/otp/verify",
            json={"challenge_id": enrolled["challenge_id"], "code": "000000"},
            headers=headers,
        )

    login = await login_user(client, "alice")
    sent_code = sms.last_code
    sms.sent.clear()

    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": login["challenge_id"], "method": "TOTP"},
    )

    assert switched.status_code == 429
    assert sms.sent == []  # the failed switch sent nothing
    completed = await client.post(
        "/auth/login/otp", json={"challenge_id": login["challenge_id"], "code": sent_code}
    )
    assert completed.status_code == 200, completed.text


async def test_turning_two_step_off_ends_the_lock(
    client: AsyncClient, clock: FakeClock, totp_service: TotpService
) -> None:
    """With the second factor off there is nothing to guess at, and re-enrolling
    starts from a clean slate."""
    headers, _user = await auth_user(client)
    enrolled = (await client.post("/me/totp/enable", headers=headers)).json()
    for _ in range(TEST_SETTINGS.otp_max_verify_attempts):
        await client.post(
            "/me/otp/verify",
            json={"challenge_id": enrolled["challenge_id"], "code": "000000"},
            headers=headers,
        )
    assert (await client.post("/me/totp/enable", headers=headers)).status_code == 429

    disabled = await client.post("/me/otp/disable", headers=headers)

    assert disabled.status_code == 200
    assert (await client.post("/me/totp/enable", headers=headers)).status_code == 200
