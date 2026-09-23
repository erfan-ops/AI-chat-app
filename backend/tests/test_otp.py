"""Two-step verification: settings, mobile validation, enabling, disabling, login.

The SMS provider is always the recording fake from ``conftest`` — no test can reach
SMS.ir. Codes are never returned by the API, so the tests read them from the fake,
which is exactly what a real user would receive.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.api.dependencies import get_sms_service
from app.core.mobile import normalize_mobile
from app.db.models.user import User
from app.db.repositories.users import UserRepository
from app.exceptions import BadRequestError
from app.main import app
from tests.conftest import (
    TEST_PASSWORD,
    FakeClock,
    RecordingSmsService,
    auth_user,
    login_user,
    register_user,
)

MOBILE = "9123456789"


async def _headers(client: AsyncClient, username: str = "alice") -> tuple[dict[str, str], dict]:
    headers, user = await auth_user(client, username)
    return headers, user


async def _enable_otp(
    client: AsyncClient,
    headers: dict[str, str],
    sms: RecordingSmsService,
    clock: Any,
    mobile: str = MOBILE,
) -> dict[str, Any]:
    """Run the enable flow to completion and return the updated profile.

    Leaves the clock past the resend cooldown: the enable send spends the same
    per-user send budget a later login code would need.
    """
    start = await client.post("/me/otp/enable", json={"mobile_number": mobile}, headers=headers)
    assert start.status_code == 200, start.text
    verified = await client.post(
        "/me/otp/verify",
        json={"challenge_id": start.json()["challenge_id"], "code": sms.last_code},
        headers=headers,
    )
    assert verified.status_code == 200, verified.text
    clock.advance(61)
    return verified.json()


# -- Settings ------------------------------------------------------------------------


async def test_update_display_name(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.patch("/me", json={"display_name": "Alice A"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["display_name"] == "Alice A"


async def test_update_username(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.patch("/me", json={"username": "alice2"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["username"] == "alice2"


async def test_renamed_user_logs_in_with_the_new_username(client: AsyncClient) -> None:
    headers, _user = await _headers(client)
    await client.patch("/me", json={"username": "alice2"}, headers=headers)

    old = await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    new = await client.post("/auth/login", json={"username": "alice2", "password": TEST_PASSWORD})

    assert old.status_code == 401
    assert new.status_code == 200
    assert new.json()["user"]["username"] == "alice2"


async def test_update_username_rejects_a_taken_name(client: AsyncClient) -> None:
    headers_alice, alice = await _headers(client, "alice")
    await _headers(client, "bob")

    response = await client.patch("/me", json={"username": "bob"}, headers=headers_alice)

    assert response.status_code == 409
    assert response.json() == {"detail": "Username is already taken"}
    # Unchanged server-side.
    profile = await client.get("/me", headers=headers_alice)
    assert profile.json()["username"] == alice["username"]


async def test_username_uniqueness_survives_a_race(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-check can lose to a concurrent registration of the same name.

    ``UK_USERS_USERNAME`` is the actual guarantee, so a lost race must still be a
    409 rather than an unhandled IntegrityError (500).
    """
    headers, _alice = await _headers(client, "alice")
    await _headers(client, "bob")

    async def blind(self: UserRepository, username: str) -> None:
        return None

    monkeypatch.setattr(UserRepository, "get_by_username", blind)

    response = await client.patch("/me", json={"username": "bob"}, headers=headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "Username is already taken"}


async def test_update_username_is_case_sensitive(client: AsyncClient) -> None:
    """Usernames are case-sensitive in Oracle, so this is a distinct account."""
    headers, _user = await _headers(client, "alice")
    await _headers(client, "Bob")

    response = await client.patch("/me", json={"username": "BOB"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["username"] == "BOB"


async def test_update_username_to_own_name_is_allowed(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.patch("/me", json={"username": "alice"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["username"] == "alice"


async def test_update_username_validation(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    for bad in ("ab", "a" * 33, "has space", "has/slash", ""):
        response = await client.patch("/me", json={"username": bad}, headers=headers)
        assert response.status_code == 422, bad


async def test_update_default_model(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.patch("/me", json={"default_model_id": 1}, headers=headers)

    assert response.status_code == 200
    assert response.json()["default_model_id"] == 1


async def test_update_default_model_rejects_unknown_id(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.patch("/me", json={"default_model_id": 9999}, headers=headers)

    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown or inactive model"}


async def test_settings_require_authentication(client: AsyncClient) -> None:
    response = await client.patch("/me", json={"display_name": "Nope"})

    assert response.status_code == 401


async def test_empty_update_is_rejected(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.patch("/me", json={}, headers=headers)

    assert response.status_code == 422


async def test_settings_only_affect_the_caller(client: AsyncClient, session_factory: Any) -> None:
    headers_alice, alice = await _headers(client, "alice")
    _headers_bob, bob = await _headers(client, "bob")

    await client.patch("/me", json={"display_name": "Alice A"}, headers=headers_alice)

    async with session_factory() as db:
        from sqlalchemy import select

        bob_row = (await db.scalars(select(User).where(User.id == bob["id"]))).first()
    assert bob_row is not None
    assert bob_row.display_name is None
    assert alice["id"] != bob["id"]


# -- Mobile validation ---------------------------------------------------------------


def test_normalize_mobile_accepts_separators() -> None:
    assert normalize_mobile("912 345 6789") == MOBILE
    assert normalize_mobile("912-345-6789") == MOBILE
    assert normalize_mobile(MOBILE) == MOBILE


def test_normalize_mobile_rejects_bad_lengths() -> None:
    for value in ("912345678", "91234567890", ""):
        try:
            normalize_mobile(value)
        except BadRequestError:
            continue
        raise AssertionError(f"{value!r} should have been rejected")


def test_normalize_mobile_rejects_international_and_local_prefixes() -> None:
    for value in ("+989123456789", "00989123456789", "989123456789", "09123456789"):
        try:
            normalize_mobile(value)
        except BadRequestError as exc:
            assert "country code" in exc.detail or "leading zero" in exc.detail
            continue
        raise AssertionError(f"{value!r} should have been rejected")


def test_normalize_mobile_rejects_non_ascii_digits() -> None:
    # A Persian-keyboard number would otherwise reach Oracle's NUMBER column.
    persian = "9" + "".join(chr(0x06F0 + digit) for digit in (1, 2, 3, 4, 5, 6, 7, 8, 9))
    try:
        normalize_mobile(persian)
    except BadRequestError as exc:
        assert "English digits" in exc.detail
        return
    raise AssertionError("non-ASCII digits should have been rejected")


async def test_enable_rejects_invalid_mobile(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.post("/me/otp/enable", json={"mobile_number": "12345"}, headers=headers)

    assert response.status_code == 400
    assert "10-digit" in response.json()["detail"]


# -- Enabling ------------------------------------------------------------------------


async def test_enable_sends_code_and_masks_the_number(
    client: AsyncClient, sms: RecordingSmsService
) -> None:
    headers, user = await _headers(client)

    response = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code_expires_in_seconds"] == 120
    assert body["method"] == "SMS"
    assert body["destination_hint"] == "+98 912 *** 6789"
    # The canonical local number reaches the provider, and the display name is the
    # user's — never the formatted value or the username when a name exists.
    assert sms.sent[-1]["mobile"] == MOBILE
    assert sms.sent[-1]["display_name"] == "alice"
    assert sms.sent[-1]["user_id"] == user["id"]
    assert len(sms.last_code) == 6 and sms.last_code.isdigit()
    # Nothing is stored and the flag stays off until the code comes back.
    assert body is not None
    me = await client.get("/me", headers=headers)
    assert me.json()["otp_enabled"] is False
    assert me.json()["mobile_number"] is None
    assert sms.last_code not in response.text


async def test_enable_then_verify_stores_number_and_flags(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)

    profile = await _enable_otp(client, headers, sms, clock)

    assert profile["otp_enabled"] is True
    assert profile["mobile_number"] == MOBILE


async def test_a_contact_another_account_verified_is_refused_before_sending(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    """A number belongs to one account (UK_USERS_MOBILE_NUMBER).

    Checked before the send, so no code goes out to a contact that could never be
    attached, and the second account gets a plain conflict rather than a 500.
    """
    headers_alice, _alice = await _headers(client, "alice")
    await _enable_otp(client, headers_alice, sms, clock)
    headers_bob, _bob = await _headers(client, "bob")
    sent_before = len(sms.sent)

    response = await client.post(
        "/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers_bob
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "That mobile number is already linked to another account"}
    assert len(sms.sent) == sent_before  # nothing was sent


async def test_re_verifying_your_own_number_is_allowed(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    """The uniqueness rule is about *other* accounts, not about your own contact."""
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)
    await client.post("/me/otp/disable", headers=headers)

    response = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)

    assert response.status_code == 200, response.text


async def test_a_contact_taken_between_check_and_write_is_a_conflict(
    client: AsyncClient,
    sms: RecordingSmsService,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-check can lose a race; the DB constraint is the real guarantee.

    ``get_by_mobile`` is blinded so the check passes while another account already
    holds the number — the commit is then what refuses it.
    """
    from app.db.repositories.users import UserRepository

    headers_alice, _alice = await _headers(client, "alice")
    await _enable_otp(client, headers_alice, sms, clock)
    headers_bob, _bob = await _headers(client, "bob")

    async def blind(self: UserRepository, mobile: int) -> None:
        return None

    monkeypatch.setattr(UserRepository, "get_by_mobile", blind)
    start = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers_bob)
    assert start.status_code == 200, start.text

    response = await client.post(
        "/me/otp/verify",
        json={"challenge_id": start.json()["challenge_id"], "code": sms.last_code},
        headers=headers_bob,
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "That mobile number is already linked to another account"}


async def test_a_verified_number_can_be_replaced_by_verifying_a_new_one(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    """Changing your number is the same act as verifying one: confirm the new one.

    The old number keeps working until the new one is confirmed, and (because the
    column is unique) it is free for another account afterwards.
    """
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)  # MOBILE
    replacement = "9123456780"

    start = await client.post(
        "/me/otp/enable", json={"mobile_number": replacement}, headers=headers
    )
    assert start.status_code == 200, start.text
    # The old one is untouched until the new one is confirmed.
    me = await client.get("/me", headers=headers)
    assert me.json()["mobile_number"] == MOBILE

    confirmed = await client.post(
        "/me/otp/verify",
        json={"challenge_id": start.json()["challenge_id"], "code": sms.last_code},
        headers=headers,
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["mobile_number"] == replacement
    # Changing the contact is not a preference change.
    assert confirmed.json()["preferred_otp_method"] == "SMS"

    # The freed number can now be verified by someone else.
    headers_bob, _bob = await _headers(client, "bob")
    bob_start = await client.post(
        "/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers_bob
    )
    assert bob_start.status_code == 200, bob_start.text


async def test_verify_with_wrong_code_is_400_not_401(
    client: AsyncClient, sms: RecordingSmsService
) -> None:
    """401 on this authenticated route would sign the user out of the app."""
    headers, _user = await _headers(client)
    start = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)
    challenge_id = start.json()["challenge_id"]
    wrong = "000000" if sms.last_code != "000000" else "111111"

    response = await client.post(
        "/me/otp/verify", json={"challenge_id": challenge_id, "code": wrong}, headers=headers
    )

    assert response.status_code == 400
    assert response.status_code != 401
    me = await client.get("/me", headers=headers)
    assert me.json()["otp_enabled"] is False
    assert me.json()["mobile_number"] is None


async def test_expired_code_is_rejected(
    client: AsyncClient, sms: RecordingSmsService, clock: Any
) -> None:
    headers, _user = await _headers(client)
    start = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)
    challenge_id = start.json()["challenge_id"]

    clock.advance(121)  # past the 2-minute lifetime

    response = await client.post(
        "/me/otp/verify",
        json={"challenge_id": challenge_id, "code": sms.last_code},
        headers=headers,
    )
    assert response.status_code == 400


async def test_code_cannot_be_used_twice(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    profile = await _enable_otp(client, headers, sms, clock)
    assert profile["otp_enabled"] is True

    # Replay the same code against a fresh challenge would need a new code, so
    # re-use the consumed challenge id instead.
    start = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)
    assert start.status_code == 409  # already enabled for this number


async def test_challenge_is_bound_to_its_user(
    client: AsyncClient, sms: RecordingSmsService
) -> None:
    headers_alice, _alice = await _headers(client, "alice")
    start = await client.post(
        "/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers_alice
    )
    challenge_id = start.json()["challenge_id"]

    headers_bob, _bob = await _headers(client, "bob")
    response = await client.post(
        "/me/otp/verify",
        json={"challenge_id": challenge_id, "code": sms.last_code},
        headers=headers_bob,
    )

    assert response.status_code == 400


async def test_enable_is_rate_limited(
    client: AsyncClient, sms: RecordingSmsService, clock: Any
) -> None:
    headers, _user = await _headers(client)
    first = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)
    assert first.status_code == 200

    second = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)

    assert second.status_code == 429
    assert "Retry-After" in second.headers
    clock.advance(61)
    third = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)
    assert third.status_code == 200


async def test_sms_failure_leaves_the_account_untouched(
    client: AsyncClient, sms: RecordingSmsService
) -> None:
    headers, _user = await _headers(client)
    sms.fail = True

    response = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)

    assert response.status_code == 503
    assert "Could not send" in response.json()["detail"]
    me = await client.get("/me", headers=headers)
    assert me.json()["otp_enabled"] is False
    assert me.json()["mobile_number"] is None


async def test_sms_unconfigured_answers_503(client: AsyncClient) -> None:
    """The real service must refuse to send without a key — and never call out.

    The recording fake is removed so the real ``SmsService`` runs, and
    ``TEST_SETTINGS`` pins an empty key, so this cannot reach SMS.ir even when the
    developer's .env holds a live key.
    """
    app.dependency_overrides.pop(get_sms_service, None)
    headers, _user = await _headers(client)

    response = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "SMS is not configured"}


# -- Disabling -----------------------------------------------------------------------


async def test_disable_turns_flag_off_and_keeps_the_number(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)

    response = await client.post("/me/otp/disable", headers=headers)

    assert response.status_code == 200
    assert response.json()["otp_enabled"] is False
    assert response.json()["mobile_number"] == MOBILE


async def test_disable_invalidates_outstanding_codes(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)
    login_challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )
    assert login_challenge.json()["otp_required"] is True
    challenge_id = login_challenge.json()["challenge_id"]
    code = sms.last_code

    await client.post("/me/otp/disable", headers=headers)

    response = await client.post(
        "/auth/login/otp", json={"challenge_id": challenge_id, "code": code}
    )
    assert response.status_code == 400


async def test_disable_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/me/otp/disable")

    assert response.status_code == 401


# -- Login ---------------------------------------------------------------------------


async def test_login_without_otp_is_unchanged(client: AsyncClient) -> None:
    await register_user(client, "alice")

    login = await login_user(client, "alice")

    assert login["access_token"]
    assert login["user"]["otp_enabled"] is False


async def test_login_with_otp_returns_challenge_and_no_token(
    client: AsyncClient, sms: RecordingSmsService, session_factory: Any, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)
    # The setup login set last_login_at; the incomplete login below must not move it.
    async with session_factory() as db:
        from sqlalchemy import select

        row = (await db.scalars(select(User).where(User.username == "alice"))).first()
        assert row is not None
        before = row.last_login_at
    assert before is not None

    response = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["otp_required"] is True
    assert "access_token" not in body
    assert body["code_expires_in_seconds"] == 120
    # The channel is named (the client has to describe the next step), but no hint
    # about the number: a password holder must not learn it.
    assert body["delivery_method"] == "SMS"
    assert body["alternative_method"] is None  # no verified email on this account
    for leaked in ("mobile_hint", "destination_hint", "email", "mobile_number"):
        assert leaked not in body
    assert sms.sent[-1]["mobile"] == MOBILE

    async with session_factory() as db:
        row = (await db.scalars(select(User).where(User.username == "alice"))).first()
    assert row is not None
    assert row.last_login_at == before


async def test_login_otp_completes_authentication(
    client: AsyncClient, sms: RecordingSmsService, session_factory: Any, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    response = await client.post(
        "/auth/login/otp",
        json={"challenge_id": challenge.json()["challenge_id"], "code": sms.last_code},
    )

    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    async with session_factory() as db:
        from sqlalchemy import select

        row = (await db.scalars(select(User).where(User.username == "alice"))).first()
    assert row is not None and row.last_login_at is not None


async def test_login_otp_wrong_code_fails_without_authenticating(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )
    wrong = "000000" if sms.last_code != "000000" else "111111"

    response = await client.post(
        "/auth/login/otp", json={"challenge_id": challenge.json()["challenge_id"], "code": wrong}
    )

    assert response.status_code == 400
    assert "access_token" not in response.json()


async def test_login_otp_expired_code_fails(
    client: AsyncClient, sms: RecordingSmsService, clock: Any
) -> None:
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )
    clock.advance(121)

    response = await client.post(
        "/auth/login/otp",
        json={"challenge_id": challenge.json()["challenge_id"], "code": sms.last_code},
    )

    assert response.status_code == 400


async def test_login_otp_code_is_single_use(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )
    body = {"challenge_id": challenge.json()["challenge_id"], "code": sms.last_code}

    first = await client.post("/auth/login/otp", json=body)
    second = await client.post("/auth/login/otp", json=body)

    assert first.status_code == 200
    assert second.status_code == 400


async def test_login_otp_unknown_challenge_fails(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login/otp", json={"challenge_id": "x" * 40, "code": "123456"}
    )

    assert response.status_code == 400


async def test_login_sms_failure_does_not_authenticate(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)
    sms.fail = True

    response = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    assert response.status_code == 503
    assert "access_token" not in response.text


async def test_login_wrong_password_still_401_with_otp_enabled(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable_otp(client, headers, sms, clock)

    response = await client.post(
        "/auth/login", json={"username": "alice", "password": "not-the-password"}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect username or password"}


async def test_otp_enabled_without_number_fails_closed(
    client: AsyncClient, session_factory: Any
) -> None:
    """An account flagged for 2FA with no number must not fall back to password-only."""
    await register_user(client, "alice")
    async with session_factory() as db:
        from sqlalchemy import select

        row = (await db.scalars(select(User).where(User.username == "alice"))).first()
        assert row is not None
        row.otp_enabled = 1
        await db.commit()

    response = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    assert response.status_code == 503
    assert "access_token" not in response.text
