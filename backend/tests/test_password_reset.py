"""Password recovery: the honest flow, and every way of getting around it.

The whole design rests on one invariant — **the only thing that can change a password
is an authorization this API minted itself, after checking a code itself**. So most of
what follows are attempts to get past that: a fabricated token, a real login token, a
client that asserts the code was verified, a token issued for a different account, a
token used twice, a token that expired a second ago. The rest are the honest paths:
each recovery channel (SMS, email, the authenticator the user already has), the shape
of the answers, and the fact that a reset is not a way to learn who has an account here.

Everything runs on ``FakeClock``, so expiry, cooldowns and the ten-minute authorization
are exercised without waiting for them — and without a real SMS, email or NTP call.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_email_service, get_otp_service
from app.core.contact import OTP_METHODS
from app.core.security import PasswordManager
from app.db.models.otp_log import OtpLog
from app.db.models.user import User
from app.exceptions import RateLimitError
from app.main import app
from app.services.otp_service import OtpService
from app.services.totp_service import TotpService
from tests.conftest import (
    TEST_PASSWORD,
    TEST_SETTINGS,
    FakeClock,
    FakeTimeService,
    RecordingEmailService,
    RecordingSmsService,
    auth_user,
    login_user,
)

EMAIL = "alice@example.com"
MOBILE = "9123456789"
NEW_PASSWORD = "a-brand-new-password"
OTHER_PASSWORD = "somebody-elses-password"

CODE_TTL = TEST_SETTINGS.otp_code_ttl_seconds
COOLDOWN = TEST_SETTINGS.otp_resend_cooldown_seconds
RESET_TTL = TEST_SETTINGS.password_reset_authorization_ttl_seconds

# What every answer to a request says, whether or not the account exists.
GENERIC_MESSAGE = "If that account can be recovered, a verification code has been sent."


# -- Helpers ------------------------------------------------------------------------


async def _set_contact(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    *,
    email: str | None = None,
    mobile: str | None = None,
    otp_enabled: int = 1,
    preferred: str | None = None,
) -> None:
    """Give the account the contact the enable flow would have left, without it.

    The enable flow itself is exercised elsewhere; here the point is what recovery
    does with a verified contact, so the state is written directly — including the
    preference it sets, which is the channel that was just verified.
    """
    async with session_factory() as db:
        user = (await db.scalars(select(User).where(User.id == user_id))).first()
        assert user is not None
        if email is not None:
            user.email = email
        if mobile is not None:
            user.mobile_number = int(mobile)
        user.otp_enabled = otp_enabled
        user.preferred_otp_method = preferred or ("EMAIL" if email else "SMS" if mobile else None)
        await db.commit()


async def _stored(session_factory: async_sessionmaker[AsyncSession], user_id: int) -> User:
    async with session_factory() as db:
        user = (await db.scalars(select(User).where(User.id == user_id))).first()
        assert user is not None
        return user


async def _request(
    client: AsyncClient, identifier: str = "alice", method: str | None = None
) -> Response:
    body: dict[str, Any] = {"identifier": identifier}
    if method is not None:
        body["method"] = method
    return await client.post("/auth/password-reset/request", json=body)


async def _start(
    client: AsyncClient,
    fake: RecordingSmsService | RecordingEmailService,
    identifier: str = "alice",
    method: str = "EMAIL",
) -> tuple[str, str]:
    """Request a code and return ``(challenge_id, code)`` as the user receives it."""
    response = await _request(client, identifier, method)
    assert response.status_code == 200, response.text
    challenge_id = response.json()["challenge_id"]
    assert challenge_id, response.text
    return challenge_id, fake.last_code


async def _verify(client: AsyncClient, challenge_id: str, code: str) -> Response:
    return await client.post(
        "/auth/password-reset/verify", json={"challenge_id": challenge_id, "code": code}
    )


async def _token(client: AsyncClient, fake: Any, method: str = "EMAIL") -> str:
    """Run the first two steps and return the authorization."""
    challenge_id, code = await _start(client, fake, method=method)
    verified = await _verify(client, challenge_id, code)
    assert verified.status_code == 200, verified.text
    return str(verified.json()["reset_token"])


async def _complete(client: AsyncClient, token: str, password: str = NEW_PASSWORD) -> Response:
    return await client.post(
        "/auth/password-reset/complete",
        json={"reset_token": token, "new_password": password},
    )


async def _login_starts(
    client: AsyncClient, username: str = "alice", password: str = TEST_PASSWORD
) -> Response:
    return await client.post("/auth/login", json={"username": username, "password": password})


async def _rows(session_factory: async_sessionmaker[AsyncSession]) -> list[OtpLog]:
    async with session_factory() as db:
        return list((await db.scalars(select(OtpLog).order_by(OtpLog.id))).all())


# -- The honest flow ----------------------------------------------------------------


async def test_a_reset_by_email_changes_the_password(
    client: AsyncClient,
    sms: RecordingSmsService,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    started = await _request(client, "alice", "EMAIL")

    assert started.status_code == 200, started.text
    body = started.json()
    assert set(body) == {"methods", "method", "challenge_id", "code_expires_in_seconds", "message"}
    assert body["method"] == "EMAIL"
    assert body["message"] == GENERIC_MESSAGE
    assert body["code_expires_in_seconds"] == CODE_TTL
    # The code went to the account's verified address, and nowhere else.
    assert [entry["recipient"] for entry in email.sent] == [EMAIL]
    assert [entry["purpose"] for entry in email.sent] == ["password_reset"]
    assert sms.sent == []

    verified = await _verify(client, body["challenge_id"], email.last_code)

    assert verified.status_code == 200, verified.text
    authorization = verified.json()
    assert set(authorization) == {"reset_token", "expires_in_seconds"}
    assert authorization["expires_in_seconds"] == RESET_TTL
    # An opaque random string, not a signed token of any kind: nothing about the user
    # can be read out of it, and it is accepted by exactly one endpoint.
    reset_token = authorization["reset_token"]
    assert "." not in reset_token
    assert len(reset_token) >= 32

    completed = await _complete(client, reset_token)

    assert completed.status_code == 200, completed.text
    assert completed.json()["username"] == "alice"
    assert "password_hash" not in completed.text

    # The new password works and the old one does not.
    assert (await _login_starts(client, password=NEW_PASSWORD)).status_code == 200
    assert (await _login_starts(client, password=TEST_PASSWORD)).status_code == 401


async def test_a_reset_by_sms_changes_the_password(
    client: AsyncClient,
    sms: RecordingSmsService,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], mobile=MOBILE)

    challenge_id, code = await _start(client, sms, method="SMS")
    verified = await _verify(client, challenge_id, code)

    assert verified.status_code == 200, verified.text
    assert [entry["mobile"] for entry in sms.sent] == [MOBILE]
    assert email.sent == []
    assert (await _complete(client, verified.json()["reset_token"])).status_code == 200
    assert (await _login_starts(client, password=NEW_PASSWORD)).status_code == 200


async def test_a_reset_with_the_existing_authenticator(
    client: AsyncClient,
    sms: RecordingSmsService,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
    totp_service: TotpService,
    time_service: FakeTimeService,
) -> None:
    """Recovery by authenticator uses the secret already enrolled, and does not rotate it."""
    headers, user = await auth_user(client)
    enrolment = await client.post("/me/totp/enable", headers=headers)
    assert enrolment.status_code == 200, enrolment.text
    secret = enrolment.json()["secret"]
    confirmed = await client.post(
        "/me/otp/verify",
        json={
            "challenge_id": enrolment.json()["challenge_id"],
            "code": totp_service.code_at(
                secret=secret, unix_time=time_service.get_current_unix_time()
            ),
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    started = await _request(client, "alice", "TOTP")

    assert started.status_code == 200, started.text
    challenge_id = started.json()["challenge_id"]
    assert challenge_id
    # Nothing is sent for an authenticator code: the user's own app produces it.
    assert sms.sent == [] and email.sent == []

    code = totp_service.code_at(secret=secret, unix_time=time_service.get_current_unix_time())
    verified = await _verify(client, challenge_id, code)

    assert verified.status_code == 200, verified.text
    assert (await _complete(client, verified.json()["reset_token"])).status_code == 200

    # The secret is the source of truth and is never replaced by a recovery.
    assert (await _stored(session_factory, user["id"])).totp_secret == secret
    # And the authenticator still works, with the password that was just set.
    login = await _login_starts(client, password=NEW_PASSWORD)
    assert login.status_code == 200, login.text
    assert login.json()["otp_required"] is True
    second = await client.post(
        "/auth/login/otp",
        json={
            "challenge_id": login.json()["challenge_id"],
            "code": totp_service.code_at(
                secret=secret, unix_time=time_service.get_current_unix_time()
            ),
        },
    )
    assert second.status_code == 200, second.text


async def test_the_identifier_may_be_the_username_or_the_email(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    # Typed the way a person types it: padded, and in whatever case they use.
    by_email = await _request(client, "  ALICE@Example.COM ", "EMAIL")

    assert by_email.status_code == 200, by_email.text
    assert by_email.json()["challenge_id"]
    # And it went to the address as it is stored, not as it was typed.
    assert [entry["recipient"] for entry in email.sent] == [EMAIL]


async def test_requesting_without_a_method_only_reports_the_options(
    client: AsyncClient,
    sms: RecordingSmsService,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    response = await _request(client, "alice")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["methods"] == ["EMAIL"]
    assert body["method"] is None
    assert body["challenge_id"] is None
    # Nothing was sent: this step asks, it does not act.
    assert email.sent == [] and sms.sent == []


async def test_a_verified_contact_is_recoverable_with_two_step_verification_off(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Turning 2FA off must not lock anyone out of their own account.

    The contact stays verified (``two_factor_service.disable`` keeps it), so it stays a
    way back in — the only one such an account has.
    """
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL, otp_enabled=0)

    challenge_id, code = await _start(client, email)

    assert (await _verify(client, challenge_id, code)).status_code == 200


async def test_the_history_records_the_reset(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    token = await _token(client, email)
    assert (await _complete(client, token)).status_code == 200

    rows = [row for row in await _rows(session_factory) if row.purpose == "password_reset"]
    assert len(rows) == 1
    assert rows[0].user_id == user["id"]
    assert rows[0].method == "EMAIL"
    assert rows[0].consumed == 1
    assert rows[0].consumed_at is not None
    # The code itself is never in the history — only an HMAC, keyed per process.
    assert email.last_code not in (rows[0].code_hash or "")


# -- The code step cannot be skipped or faked ---------------------------------------


async def test_verifying_does_not_change_the_password(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    await _token(client, email)

    # The code was verified, and nothing else happened: the old password still works.
    assert (await _login_starts(client, password=TEST_PASSWORD)).status_code == 200
    assert (await _login_starts(client, password=NEW_PASSWORD)).status_code == 401


async def test_the_client_cannot_assert_that_the_code_was_verified(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A request that says the code was verified is worth exactly nothing."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    challenge_id, code = await _start(client, email)

    response = await client.post(
        "/auth/password-reset/complete",
        json={
            "reset_token": "x" * 43,
            "new_password": NEW_PASSWORD,
            "otp_verified": True,
            "otpVerified": True,
            "verified": True,
            "challenge_id": challenge_id,
            "code": code,
            "username": "alice",
            "user_id": user["id"],
        },
    )

    assert response.status_code == 400, response.text
    assert (await _login_starts(client, password=TEST_PASSWORD)).status_code == 200
    # The claimed flags changed nothing about the challenge either: it is still there,
    # and still needs the real code.
    assert (await _verify(client, challenge_id, code)).status_code == 200


async def test_a_fabricated_authorization_is_refused(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    for token in ("x" * 43, "0" * 64, "not-a-token-at-all-really-1234"):
        response = await _complete(client, token)
        assert response.status_code == 400, response.text
    assert (await _login_starts(client, password=TEST_PASSWORD)).status_code == 200


async def test_a_login_token_is_not_a_reset_authorization(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A real, valid access token proves a session — not that a code was verified."""
    _headers, user = await auth_user(client)
    # Signed in first: enabling a contact turns two-step verification on, and this is
    # about a session, not about the second step.
    access_token = (await login_user(client, "alice"))["access_token"]
    await _set_contact(session_factory, user["id"], email=EMAIL)

    response = await _complete(client, access_token)

    assert response.status_code == 400, response.text
    # And it is still a working session, just not a way to change a password here.
    me = await client.get("/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "alice"


async def test_a_reset_authorization_is_not_a_session(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    token = await _token(client, email)

    for path in ("/me", "/conversations", "/memories"):
        response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, (path, response.text)


async def test_a_wrong_code_is_refused_and_changes_nothing(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    challenge_id, _code = await _start(client, email)
    wrong = "000000" if email.last_code != "000000" else "111111"

    response = await _verify(client, challenge_id, wrong)

    assert response.status_code == 400, response.text
    # The same wording an expired or used challenge gets: nothing to learn from it.
    assert response.json()["detail"] == "That verification code is incorrect or has expired"
    assert (await _login_starts(client, password=TEST_PASSWORD)).status_code == 200


async def test_too_many_wrong_codes_kill_the_challenge(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    challenge_id, code = await _start(client, email)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(TEST_SETTINGS.otp_max_verify_attempts):
        assert (await _verify(client, challenge_id, wrong)).status_code == 400

    # The challenge is gone, so even the right code is now nothing.
    assert (await _verify(client, challenge_id, code)).status_code == 400
    assert (await _complete(client, "x" * 43)).status_code == 400


async def test_a_code_stops_working_when_it_expires(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    challenge_id, code = await _start(client, email)

    clock.advance(CODE_TTL + 1)

    assert (await _verify(client, challenge_id, code)).status_code == 400


async def test_a_recovery_code_cannot_be_redeemed_as_a_login_code(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Challenges are bound to a purpose, in both directions."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    challenge_id, code = await _start(client, email)

    # The reset code, offered to the login endpoint.
    login = await client.post("/auth/login/otp", json={"challenge_id": challenge_id, "code": code})
    assert login.status_code == 400, login.text

    # And a login code, offered to the reset endpoint.
    login_start = await _login_starts(client, password=TEST_PASSWORD)
    assert login_start.json()["otp_required"] is True
    login_challenge = login_start.json()["challenge_id"]
    assert (await _verify(client, login_challenge, email.last_code)).status_code == 400

    # Neither consumed the other: the reset challenge is still good.
    assert (await _verify(client, challenge_id, code)).status_code == 200


# -- The authorization itself --------------------------------------------------------


async def test_the_authorization_expires_after_ten_minutes(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    token = await _token(client, email)

    # Nine minutes and fifty-nine seconds: still good.
    clock.advance(RESET_TTL - 1)
    assert (await _complete(client, token)).status_code == 200


async def test_the_authorization_is_dead_a_second_after_that(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    token = await _token(client, email)

    clock.advance(RESET_TTL + 1)
    response = await _complete(client, token)

    assert response.status_code == 400, response.text
    assert (await _login_starts(client, password=TEST_PASSWORD)).status_code == 200


async def test_an_authorization_can_only_be_spent_once(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    token = await _token(client, email)

    assert (await _complete(client, token)).status_code == 200
    second = await _complete(client, token, OTHER_PASSWORD)

    assert second.status_code == 400, second.text
    # And the second attempt changed nothing: the first password still stands.
    assert (await _login_starts(client, password=NEW_PASSWORD)).status_code == 200
    assert (await _login_starts(client, password=OTHER_PASSWORD)).status_code == 401


async def test_completing_an_authorization_invalidates_the_others(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    """Two authorizations for one account: spending one spends the rest."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    first = await _token(client, email)
    clock.advance(COOLDOWN + 1)
    second = await _token(client, email)

    assert (await _complete(client, second)).status_code == 200
    assert (await _complete(client, first, OTHER_PASSWORD)).status_code == 400


async def test_a_reset_drops_the_sign_in_codes_already_in_flight(
    client: AsyncClient,
    sms: RecordingSmsService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], mobile=MOBILE)
    login_start = await _login_starts(client, password=TEST_PASSWORD)
    live_login_challenge = login_start.json()["challenge_id"]

    token = await _token(client, sms, method="SMS")
    assert (await _complete(client, token)).status_code == 200

    # The code that was in flight for the old password is no longer a way in.
    retired = await client.post(
        "/auth/login/otp", json={"challenge_id": live_login_challenge, "code": sms.last_code}
    )
    assert retired.status_code == 400, retired.text


async def test_the_authorization_expires_even_without_being_used(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    """Nothing prunes it early and nothing keeps it alive: it is bounded by the clock."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    token = await _token(client, email)

    clock.advance(RESET_TTL * 3)
    assert (await _complete(client, token)).status_code == 400


# -- Accounts are not interchangeable -----------------------------------------------


async def test_one_accounts_authorization_leaves_the_other_alone(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _alice_headers, alice = await auth_user(client, "alice")
    _bob_headers, bob = await auth_user(client, "bob", OTHER_PASSWORD)
    await _set_contact(session_factory, alice["id"], email=EMAIL)
    await _set_contact(session_factory, bob["id"], email="bob@example.com")

    token = await _token(client, email)

    completed = await _complete(client, token)
    assert completed.status_code == 200
    assert completed.json()["id"] == alice["id"]
    # Bob is untouched: his password still works, and Alice's new one is not his.
    assert (await _login_starts(client, "bob", OTHER_PASSWORD)).status_code == 200
    assert (await _login_starts(client, "bob", NEW_PASSWORD)).status_code == 401


async def test_a_challenge_belongs_to_the_account_it_was_sent_to(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One account's code cannot produce an authorization for another.

    The user is nowhere in the verify request — the account comes from the challenge
    itself — so this is a property of the store, asserted here from the outside.
    """
    _alice_headers, alice = await auth_user(client, "alice")
    _bob_headers, bob = await auth_user(client, "bob", OTHER_PASSWORD)
    await _set_contact(session_factory, alice["id"], email=EMAIL)
    await _set_contact(session_factory, bob["id"], email="bob@example.com")

    challenge_id, code = await _start(client, email)
    verified = await _verify(client, challenge_id, code)
    assert verified.status_code == 200

    # The authorization is Alice's, whoever asks: completing it changes her account.
    assert (await _complete(client, verified.json()["reset_token"])).status_code == 200
    assert (await _login_starts(client, "bob", OTHER_PASSWORD)).status_code == 200


# -- Nobody learns who has an account here ------------------------------------------


async def test_an_unknown_account_gets_the_same_answer(
    client: AsyncClient,
    sms: RecordingSmsService,
    email: RecordingEmailService,
) -> None:
    response = await _request(client, "nobody", "EMAIL")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"] == GENERIC_MESSAGE
    assert body["challenge_id"] is None
    # The same list a fully-equipped account reports, so the shape is not an oracle.
    assert body["methods"] == list(OTP_METHODS)
    assert email.sent == [] and sms.sent == []


async def test_an_account_with_no_recovery_method_answers_identically(
    client: AsyncClient,
    sms: RecordingSmsService,
    email: RecordingEmailService,
) -> None:
    """A real account nobody can reach looks exactly like one that does not exist."""
    _headers, _user = await auth_user(client)  # no contact, no authenticator
    unknown = await _request(client, "nobody", "EMAIL")
    existing = await _request(client, "alice", "EMAIL")

    assert existing.status_code == 200, existing.text
    assert existing.json() == unknown.json()
    assert email.sent == [] and sms.sent == []


async def test_a_channel_the_account_cannot_use_sends_nothing(
    client: AsyncClient,
    sms: RecordingSmsService,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    # The account has an email, not a mobile number.
    response = await _request(client, "alice", "SMS")

    assert response.status_code == 200, response.text
    assert response.json()["challenge_id"] is None
    assert response.json()["methods"] == ["EMAIL"]
    assert sms.sent == []


async def test_an_unconfigured_provider_is_not_offered(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    sms: RecordingSmsService,
) -> None:
    """A deployment that cannot send email does not pretend it did."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    off = RecordingEmailService(configured=False)
    app.dependency_overrides[get_email_service] = lambda: off

    response = await _request(client, "alice", "EMAIL")

    assert response.status_code == 200, response.text
    assert response.json()["challenge_id"] is None
    # And the options are the same as for an account nobody can reach, which is what
    # this is: indistinguishable from one that does not exist.
    assert response.json()["methods"] == list(OTP_METHODS)
    assert off.sent == [] and sms.sent == []


async def test_the_callers_own_limit_is_the_same_for_every_identifier(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A refusal that only happened for real accounts would be the oracle again.

    The per-address window counts every recovery request, not only the ones that send
    something, so an address that has spent it is refused whatever it asks about —
    and the sweep it bounds never gets far enough to map accounts to contacts.
    """
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    spent = [
        await _request(client, "nobody", "EMAIL") for _ in range(TEST_SETTINGS.otp_max_sends_per_ip)
    ]
    assert {response.status_code for response in spent} == {200}

    refused_unknown = await _request(client, "nobody", "EMAIL")
    refused_real = await _request(client, "alice", "EMAIL")

    assert refused_unknown.status_code == 429, refused_unknown.text
    assert refused_real.status_code == refused_unknown.status_code
    assert refused_real.json() == refused_unknown.json()
    # Nothing was sent for either of them.
    assert email.sent == []


async def test_one_request_spends_one_slot_in_that_window(
    client: AsyncClient,
    sms: RecordingSmsService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    """Sending is not charged twice: the request was already counted."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL, mobile=MOBILE)
    limits = {"otp_max_sends_per_ip": 2, "otp_resend_cooldown_seconds": 0}
    tight = OtpService(TEST_SETTINGS.model_copy(update=limits), clock=clock)
    app.dependency_overrides[get_otp_service] = lambda: tight

    on_email = await _request(client, "alice", "EMAIL")
    on_sms = await _request(client, "alice", "SMS")
    refused = await _request(client, "alice", "EMAIL")

    assert on_email.json()["challenge_id"] and on_sms.json()["challenge_id"]
    assert refused.status_code == 429, refused.text


async def test_recovery_requests_do_not_block_a_sign_in_code(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    """One window per concern.

    Recovery requests are counted per address, and sign-in *messages* are counted per
    address, but not in the same place: otherwise sweeping the recovery endpoint from a
    network would be a way to stop the sign-in codes of everyone behind that address.
    """
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    tight = OtpService(TEST_SETTINGS.model_copy(update={"otp_max_sends_per_ip": 2}), clock=clock)
    app.dependency_overrides[get_otp_service] = lambda: tight

    await _request(client, "nobody", "EMAIL")
    await _request(client, "nobody", "EMAIL")
    assert (await _request(client, "nobody", "EMAIL")).status_code == 429

    # Signing in still gets a code.
    startup = await _login_starts(client, password=TEST_PASSWORD)
    assert startup.status_code == 200, startup.text
    assert startup.json()["otp_required"] is True
    assert [entry["recipient"] for entry in email.sent] == [EMAIL]


async def test_the_answer_never_names_a_destination(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The caller has proved nothing about the account, so it is told nothing about it."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    sent = await _request(client, "alice", "EMAIL")

    # Not the address, not a masked version of it, and no field carrying either.
    assert EMAIL not in sent.text
    assert "al***" not in sent.text
    assert not [key for key in sent.json() if "hint" in key or "destination" in key]


# -- Recovery is its own budget ------------------------------------------------------


def _isolated(clock: FakeClock, **limits: Any) -> OtpService:
    """A store whose per-destination window is out of the way.

    That window (and the per-address one) is deliberately *shared* between the flows —
    it bounds messages, not guesses — so it is lifted here to isolate what this section
    is about: the daily caps and the cooldowns, which are counted per flow.
    """
    return OtpService(
        TEST_SETTINGS.model_copy(
            update={"otp_max_sends_per_destination": 50, "otp_resend_cooldown_seconds": 0, **limits}
        ),
        clock=clock,
    )


def _claim(service: OtpService, purpose: str, *, user_id: int = 1) -> str:
    challenge_id, _code = service.claim(
        user_id=user_id, purpose=purpose, method="EMAIL", destination=EMAIL, client_ip=None
    )
    return challenge_id


def test_exhausting_the_recovery_allowance_leaves_signing_in_alone(
    clock: FakeClock,
) -> None:
    service = _isolated(clock, password_reset_max_sends_per_day=1, otp_max_sends_per_day=2)

    _claim(service, "password_reset")
    with pytest.raises(RateLimitError):
        _claim(service, "password_reset")
    # The sign-in allowance is its own: two, exactly as configured, both still there.
    _claim(service, "login")
    _claim(service, "login")
    with pytest.raises(RateLimitError):
        _claim(service, "login")


def test_exhausting_the_sign_in_allowance_leaves_recovery_alone(
    clock: FakeClock,
) -> None:
    service = _isolated(clock, otp_max_sends_per_day=1, password_reset_max_sends_per_day=3)

    _claim(service, "login")
    with pytest.raises(RateLimitError):
        _claim(service, "login")
    # A user who cannot get another sign-in code today can still recover the account.
    for _ in range(3):
        _claim(service, "password_reset")
    with pytest.raises(RateLimitError):
        _claim(service, "password_reset")


def test_a_reset_send_does_not_consume_the_login_cooldown(clock: FakeClock) -> None:
    """The spec's own example: a recovery code at 20:00:00, a sign-in at 20:00:30."""
    service = OtpService(TEST_SETTINGS, clock=clock)
    _claim(service, "password_reset")
    clock.advance(30)

    # Signing in is not made to wait for the recovery code's cooldown…
    _claim(service, "login")
    clock.advance(29)

    # …and the recovery code's own cooldown still holds, one second before it lapses.
    with pytest.raises(RateLimitError):
        _claim(service, "password_reset")
    clock.advance(1)
    _claim(service, "password_reset")


async def test_a_reset_then_a_sign_in_code_are_both_allowed(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    """The same example, through the API: neither request blocks the other."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    await _start(client, email)  # the recovery code, at 20:00:00

    clock.advance(30)
    startup = await _login_starts(client, password=TEST_PASSWORD)  # the sign-in, at 20:00:30

    assert startup.status_code == 200, startup.text
    assert startup.json()["otp_required"] is True

    # And back the other way: the sign-in code does not block a recovery.
    clock.advance(30)
    again = await _request(client, "alice", "EMAIL")
    assert again.status_code == 200, again.text
    assert again.json()["challenge_id"]


async def test_a_reset_send_still_obeys_its_own_cooldown(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    await _start(client, email)

    refused = await _request(client, "alice", "EMAIL")

    assert refused.status_code == 429, refused.text
    assert refused.headers["retry-after"]


async def test_a_reset_forgets_the_failed_sign_ins_it_replaced(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Failures spent on a password that no longer exists must not keep the owner out."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)

    for _ in range(5):
        assert (await _login_starts(client, password="not-the-password")).status_code == 401
    assert (await _login_starts(client, password=TEST_PASSWORD)).status_code == 429

    token = await _token(client, email)
    assert (await _complete(client, token)).status_code == 200

    assert (await _login_starts(client, password=NEW_PASSWORD)).status_code == 200


# -- The password itself -------------------------------------------------------------


async def test_a_rejected_password_does_not_spend_the_authorization(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A password the schema refuses never reaches the service, so nothing is consumed."""
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    token = await _token(client, email)

    too_short = await client.post(
        "/auth/password-reset/complete", json={"reset_token": token, "new_password": "short"}
    )
    assert too_short.status_code == 422, too_short.text

    # The user fixes the password and tries again.
    assert (await _complete(client, token)).status_code == 200


async def test_the_new_password_is_hashed_like_every_other(
    client: AsyncClient,
    email: RecordingEmailService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _headers, user = await auth_user(client)
    await _set_contact(session_factory, user["id"], email=EMAIL)
    token = await _token(client, email)

    assert (await _complete(client, token)).status_code == 200

    stored = (await _stored(session_factory, user["id"])).password_hash
    assert stored is not None and stored != NEW_PASSWORD
    assert stored.startswith("$argon2id$")
    assert PasswordManager().verify(stored, NEW_PASSWORD)


async def test_a_missing_or_empty_authorization_is_refused(
    client: AsyncClient,
) -> None:
    assert (await client.post("/auth/password-reset/complete", json={})).status_code == 422
    assert (await _complete(client, "")).status_code == 422


async def test_a_missing_identifier_is_refused(client: AsyncClient) -> None:
    assert (await client.post("/auth/password-reset/request", json={})).status_code == 422
    assert (await _request(client, "")).status_code == 422
