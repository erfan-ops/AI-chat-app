"""Email as the second OTP delivery method.

Both provider fakes come from ``conftest`` — no test can reach Resend or SMS.ir.
The wire-level tests at the bottom replace ``resend.Emails.send_async`` itself, so
the parameters the SDK would post are asserted rather than sent.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pytest
import resend
from httpx import AsyncClient
from resend.exceptions import NoContentError, RateLimitError, ResendError

from app.api.dependencies import get_email_service
from app.core.contact import parse_otp_method
from app.core.email import MAX_EMAIL_LENGTH, mask_email, normalize_email
from app.exceptions import BadRequestError
from app.main import app
from app.services.email_service import _COPY, EmailService, _expiry_text, render_otp_email
from app.services.otp_service import OtpService
from tests.conftest import (
    TEST_PASSWORD,
    TEST_SETTINGS,
    FakeClock,
    RecordingEmailService,
    RecordingSmsService,
    auth_user,
    login_user,
    register_user,
)

EMAIL = "ali@example.com"
MOBILE = "9123456789"

# Configured only for the wire-level tests: everything else runs with the pinned
# empty key in TEST_SETTINGS, so a real one can never be used.
EMAIL_CONFIGURED_SETTINGS = TEST_SETTINGS.model_copy(update={"resend_api_key": "re_test_key"})


async def _headers(client: AsyncClient, username: str = "alice") -> tuple[dict[str, str], dict]:
    headers, user = await auth_user(client, username)
    return headers, user


async def _enable(
    client: AsyncClient,
    headers: dict[str, str],
    clock: FakeClock,
    fake: RecordingSmsService | RecordingEmailService,
    *,
    method: str = "EMAIL",
    destination: str = EMAIL,
) -> dict[str, Any]:
    """Run an enable flow to completion, leaving the clock past the cooldown."""
    body: dict[str, Any] = {"method": method}
    body["email" if method == "EMAIL" else "mobile_number"] = destination
    start = await client.post("/me/otp/enable", json=body, headers=headers)
    assert start.status_code == 200, start.text
    verified = await client.post(
        "/me/otp/verify",
        json={"challenge_id": start.json()["challenge_id"], "code": fake.last_code},
        headers=headers,
    )
    assert verified.status_code == 200, verified.text
    clock.advance(61)
    return verified.json()


# -- Address validation --------------------------------------------------------------


def test_normalize_email_canonicalizes() -> None:
    assert normalize_email("  Ali@Example.COM ") == EMAIL
    assert normalize_email("ali+chat@mail.example.co.uk") == "ali+chat@mail.example.co.uk"
    # A plus tag and dots in the local part are the provider's business, not ours.
    assert normalize_email("a.b+c@example.com") == "a.b+c@example.com"


def test_normalize_email_rejects_malformed_addresses() -> None:
    for value in (
        "",
        "ali",
        "ali@",
        "@example.com",
        "a@@example.com",
        "ali@example",
        "ali @example.com",
        "ali@exa mple.com",
        ".ali@example.com",
        "ali.@example.com",
        "a..li@example.com",
        "ali@-example.com",
        "ali@example..com",
    ):
        try:
            normalize_email(value)
        except BadRequestError:
            continue
        raise AssertionError(f"{value!r} should have been rejected")


def test_normalize_email_rejects_non_ascii_and_overlong() -> None:
    # Persian characters would count as more bytes than characters in the
    # VARCHAR2(200) column, which is why the rule is ASCII-only.
    for value in ("علی@example.com", "ali@" + "a" * MAX_EMAIL_LENGTH + ".com"):
        with pytest.raises(BadRequestError):
            normalize_email(value)


def test_normalize_email_rejects_control_characters() -> None:
    """A NUL or DEL is not whitespace, so it must be rejected explicitly."""
    for value in ("a\x00b@example.com", "a\x07b@example.com", "a@ex\x01ample.com", "a\x7fb@x.com"):
        with pytest.raises(BadRequestError):
            normalize_email(value)


def test_normalize_email_accepts_exactly_the_column_width() -> None:
    longest = "a" * (MAX_EMAIL_LENGTH - len("@example.com")) + "@example.com"
    assert len(longest) == MAX_EMAIL_LENGTH
    assert normalize_email(longest) == longest


def test_mask_email_keeps_the_domain_only() -> None:
    assert mask_email(EMAIL) == "al***@example.com"
    assert mask_email("a@example.com") == "a***@example.com"
    assert mask_email("broken") == "***"


def test_parse_otp_method_defaults_and_fails_closed() -> None:
    assert parse_otp_method(None) == "SMS"
    assert parse_otp_method("EMAIL") == "EMAIL"
    assert parse_otp_method("email") == "EMAIL"
    assert parse_otp_method("carrier-pigeon") is None


# -- Enabling with an email address --------------------------------------------------


async def test_enable_by_email_sends_to_the_canonical_address(
    client: AsyncClient, email: RecordingEmailService, sms: RecordingSmsService
) -> None:
    headers, user = await _headers(client)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": " Ali@Example.com "}, headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["method"] == "EMAIL"
    assert body["destination_hint"] == "al***@example.com"
    assert body["code_expires_in_seconds"] == 120
    assert email.sent[-1]["recipient"] == EMAIL
    assert email.sent[-1]["user_id"] == user["id"]
    assert len(email.last_code) == 6 and email.last_code.isdigit()
    assert sms.sent == []  # the other channel is untouched
    assert email.last_code not in response.text


async def test_verify_stores_the_email_and_makes_it_the_default(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)

    profile = await _enable(client, headers, clock, email)

    assert profile["email"] == EMAIL
    assert profile["otp_enabled"] is True
    assert profile["preferred_otp_method"] == "EMAIL"
    # Nothing was written before the code came back.
    assert profile["mobile_number"] is None


async def test_enable_requires_the_field_matching_the_method(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    missing = await client.post("/me/otp/enable", json={"method": "EMAIL"}, headers=headers)
    mismatched = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "mobile_number": MOBILE}, headers=headers
    )

    assert missing.status_code == 422
    assert mismatched.status_code == 422


async def test_enable_rejects_an_invalid_address(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": "not-an-address"}, headers=headers
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Enter a valid email address"}


async def test_enable_the_same_address_twice_is_a_conflict(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, email)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
    )

    assert response.status_code == 409


async def test_email_failure_leaves_the_account_untouched(
    client: AsyncClient, email: RecordingEmailService
) -> None:
    headers, _user = await _headers(client)
    email.fail = True

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
    )

    assert response.status_code == 503
    assert "Could not send" in response.json()["detail"]
    me = await client.get("/me", headers=headers)
    assert me.json()["otp_enabled"] is False
    assert me.json()["email"] is None


async def test_an_address_another_account_verified_is_refused_before_sending(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
) -> None:
    """UK_USERS_EMAIL: an address belongs to one account."""
    headers_alice, _alice = await _headers(client, "alice")
    await _enable(client, headers_alice, clock, email)
    headers_bob, _bob = await _headers(client, "bob")
    sent_before = len(email.sent)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers_bob
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "That email address is already linked to another account"}
    assert len(email.sent) == sent_before  # nothing was sent


async def test_a_verified_address_can_be_replaced_by_verifying_a_new_one(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    """Changing the address is the same act as verifying one: confirm the new one."""
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, email)  # EMAIL
    replacement = "new-address@example.com"

    start = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": replacement}, headers=headers
    )
    assert start.status_code == 200, start.text
    me = await client.get("/me", headers=headers)
    assert me.json()["email"] == EMAIL  # untouched until the new one is confirmed

    confirmed = await client.post(
        "/me/otp/verify",
        json={"challenge_id": start.json()["challenge_id"], "code": email.last_code},
        headers=headers,
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["email"] == replacement
    assert confirmed.json()["preferred_otp_method"] == "EMAIL"  # a change, not a switch


async def test_email_unconfigured_answers_503(client: AsyncClient) -> None:
    """The real service must refuse to send without a key — and never call out."""
    app.dependency_overrides.pop(get_email_service, None)
    headers, _user = await _headers(client)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Email is not configured"}


# -- The saved default ---------------------------------------------------------------


async def test_default_cannot_be_set_before_the_contact_exists(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.patch("/me", json={"preferred_otp_method": "EMAIL"}, headers=headers)

    assert response.status_code == 400
    assert response.json() == {"detail": "Verify an email address first"}


async def test_default_can_be_changed_between_verified_contacts(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
) -> None:
    headers, _user = await _headers(client)
    # SMS first, so the default starts there; then verify an email as well.
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    profile = await _enable(client, headers, clock, email)

    assert profile["preferred_otp_method"] == "SMS"  # adding email did not move it
    assert profile["email"] == EMAIL

    switched = await client.patch("/me", json={"preferred_otp_method": "EMAIL"}, headers=headers)

    assert switched.status_code == 200, switched.text
    assert switched.json()["preferred_otp_method"] == "EMAIL"


async def test_default_rejects_an_unknown_method(client: AsyncClient) -> None:
    headers, _user = await _headers(client)

    response = await client.patch(
        "/me", json={"preferred_otp_method": "CARRIER_PIGEON"}, headers=headers
    )

    assert response.status_code == 422


# -- Login ---------------------------------------------------------------------------


async def test_login_uses_the_default_method(
    client: AsyncClient, email: RecordingEmailService, sms: RecordingSmsService, clock: FakeClock
) -> None:
    """Preferred EMAIL → only email is sent, and the response names both channels."""
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, email)

    response = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["otp_required"] is True
    assert body["delivery_method"] == "EMAIL"
    assert body["alternative_method"] is None  # no verified mobile on this account
    assert email.sent[-1]["recipient"] == EMAIL
    assert sms.sent == []
    assert "access_token" not in body
    # The channel is disclosed, the address never is.
    for leaked in ("email", "destination_hint", "mobile_hint", "mobile_number"):
        assert leaked not in body
    assert EMAIL not in response.text


async def test_login_offers_the_alternative_when_both_are_verified(
    client: AsyncClient, email: RecordingEmailService, sms: RecordingSmsService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    await _enable(client, headers, clock, email)

    body = (
        await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    ).json()

    assert body["delivery_method"] == "SMS"
    assert body["alternative_method"] == "EMAIL"


async def test_login_without_a_destination_for_the_default_fails_closed(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
    session_factory: Any,
) -> None:
    """A default pointing at a contact that does not exist must not fall back."""
    from sqlalchemy import select

    from app.db.models.user import User

    headers, user = await _headers(client)
    await _enable(client, headers, clock, email)
    # Simulate an out-of-band edit: default is EMAIL, but the address is gone.
    async with session_factory() as db:
        row = (await db.scalars(select(User).where(User.id == user["id"]))).first()
        assert row is not None
        row.email = None
        await db.commit()
    sent_before = len(email.sent)

    response = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    assert response.status_code == 503
    assert len(email.sent) == sent_before  # nothing new was sent
    assert sms.sent == []  # and it did not quietly use the other channel


async def test_email_login_completes(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, email)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    response = await client.post(
        "/auth/login/otp",
        json={"challenge_id": challenge.json()["challenge_id"], "code": email.last_code},
    )

    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL


async def test_email_failure_issues_no_token(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, email)
    email.fail = True

    response = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Could not send the verification code. Please try again."}
    assert "access_token" not in response.json()


# -- Switching method for one login --------------------------------------------------


async def test_switch_sends_to_the_other_channel_and_keeps_the_default(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
    session_factory: Any,
) -> None:
    from sqlalchemy import select

    from app.db.models.user import User

    headers, user = await _headers(client)
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    await _enable(client, headers, clock, email)
    # Default is SMS (it was enabled first); make it explicit for the assertion.
    await client.patch("/me", json={"preferred_otp_method": "SMS"}, headers=headers)
    first = await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    assert first.json()["delivery_method"] == "SMS"

    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": first.json()["challenge_id"], "method": "EMAIL"},
    )

    assert switched.status_code == 200, switched.text
    body = switched.json()
    assert body["delivery_method"] == "EMAIL"
    assert body["alternative_method"] == "SMS"
    assert body["challenge_id"] != first.json()["challenge_id"]
    assert email.sent[-1]["recipient"] == EMAIL
    # The saved preference is untouched by a per-login choice.
    async with session_factory() as db:
        row = (await db.scalars(select(User).where(User.id == user["id"]))).first()
    assert row is not None
    assert row.preferred_otp_method == "SMS"
    me = await client.get("/me", headers=headers)
    assert me.json()["preferred_otp_method"] == "SMS"

    # The code that arrives now is the one that works; the first one is dead.
    completed = await client.post(
        "/auth/login/otp",
        json={"challenge_id": body["challenge_id"], "code": email.last_code},
    )
    assert completed.status_code == 200, completed.text
    stale = await client.post(
        "/auth/login/otp",
        json={"challenge_id": first.json()["challenge_id"], "code": sms.last_code},
    )
    assert stale.status_code == 400


async def test_switch_email_to_sms(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    await _enable(client, headers, clock, email)
    await client.patch("/me", json={"preferred_otp_method": "EMAIL"}, headers=headers)
    first = await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    assert first.json()["delivery_method"] == "EMAIL"

    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": first.json()["challenge_id"], "method": "SMS"},
    )

    assert switched.status_code == 200, switched.text
    assert switched.json()["delivery_method"] == "SMS"
    assert sms.sent[-1]["mobile"] == MOBILE
    me = await client.get("/me", headers=headers)
    assert me.json()["preferred_otp_method"] == "EMAIL"  # unchanged


async def test_switch_provider_failure_keeps_the_original_challenge(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
) -> None:
    """A failed switch must not cost the user the code they are already holding.

    The replacement is only installed once the provider has accepted it, so this
    is the case where "send to email instead" fails mid-login: the SMS code that
    already arrived has to keep working.
    """
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    await _enable(client, headers, clock, email)
    first = await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    assert first.json()["delivery_method"] == "SMS"
    sms_code = sms.last_code
    email.fail = True

    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": first.json()["challenge_id"], "method": "EMAIL"},
    )

    assert switched.status_code == 503
    completed = await client.post(
        "/auth/login/otp",
        json={"challenge_id": first.json()["challenge_id"], "code": sms_code},
    )
    assert completed.status_code == 200, completed.text


async def test_daily_cap_holds_above_the_history_window() -> None:
    """The cap must not be silently disabled by the deque's size.

    ``_MAX_DAILY_HISTORY`` bounds the deque, so a cap configured above it used to
    evict the oldest entries and never fire — the control quietly stopped existing.
    """
    settings = TEST_SETTINGS.model_copy(
        update={"otp_max_sends_per_day": 70, "otp_resend_cooldown_seconds": 0}
    )
    service = OtpService(settings, clock=FakeClock())

    for _ in range(70):
        service.claim(user_id=1, purpose="login")

    with pytest.raises(Exception) as excinfo:
        service.claim(user_id=1, purpose="login")
    assert "Too many codes" in str(excinfo.value)


async def test_switch_to_a_channel_without_a_destination_keeps_the_original_challenge(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    """No destination for the target channel: refused before anything is sent.

    The provider-failure variant of this (the harder case) is
    ``test_switch_provider_failure_keeps_the_original_challenge``.
    """
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, email)
    first = await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})

    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": first.json()["challenge_id"], "method": "SMS"},
    )

    assert switched.status_code == 503
    completed = await client.post(
        "/auth/login/otp",
        json={"challenge_id": first.json()["challenge_id"], "code": email.last_code},
    )
    assert completed.status_code == 200, completed.text


async def test_switch_within_the_cooldown_crosses_channels(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
) -> None:
    """The cooldown is per channel: the other one is immediately available."""
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    await _enable(client, headers, clock, email)
    first = await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})

    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": first.json()["challenge_id"], "method": "EMAIL"},
    )

    assert switched.status_code == 200, switched.text


async def test_switch_back_to_a_channel_inside_its_cooldown_is_rate_limited(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    await _enable(client, headers, clock, email)
    first = await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": first.json()["challenge_id"], "method": "EMAIL"},
    )
    assert switched.status_code == 200

    # SMS was used seconds ago (fake clock), so going back is throttled.
    back = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": switched.json()["challenge_id"], "method": "SMS"},
    )

    assert back.status_code == 429
    assert "Retry-After" in back.headers


async def test_switch_unknown_or_consumed_challenges_are_400(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, email)

    unknown = await client.post(
        "/auth/login/otp/method", json={"challenge_id": "x" * 32, "method": "SMS"}
    )
    assert unknown.status_code == 400

    # An enable-purpose challenge belongs to the settings flow, not to a login.
    enable_start = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": "other@example.com"}, headers=headers
    )
    wrong_purpose = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": enable_start.json()["challenge_id"], "method": "SMS"},
    )
    assert wrong_purpose.status_code == 400
    clock.advance(61)  # that send spent the email channel's cooldown

    login_challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )
    consumed = await client.post(
        "/auth/login/otp",
        json={
            "challenge_id": login_challenge.json()["challenge_id"],
            "code": email.last_code,
        },
    )
    assert consumed.status_code == 200
    after = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": login_challenge.json()["challenge_id"], "method": "EMAIL"},
    )
    assert after.status_code == 400


async def test_switching_cannot_redirect_the_code_to_a_client_supplied_address(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
) -> None:
    """The request names a channel; the address always comes from the account."""
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    await _enable(client, headers, clock, email)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )
    assert challenge.json()["delivery_method"] == "SMS"
    before = len(email.sent)

    response = await client.post(
        "/auth/login/otp/method",
        json={
            "challenge_id": challenge.json()["challenge_id"],
            "method": "EMAIL",
            "email": "attacker@evil.example",
        },
    )

    assert response.status_code == 200, response.text
    assert len(email.sent) == before + 1
    assert email.sent[-1]["recipient"] == EMAIL  # never the supplied address


async def test_switching_to_the_same_method_is_a_throttled_resend(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    """Same channel means another send on a channel that is still cooling down."""
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, email)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    response = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": challenge.json()["challenge_id"], "method": "EMAIL"},
    )

    assert response.status_code == 429
    assert "Retry-After" in response.headers


# -- The Resend call itself ----------------------------------------------------------


@pytest.fixture
def sent_params(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture what the SDK would post, instead of posting it."""
    captured: list[dict[str, Any]] = []

    async def fake_send(params: Any, options: Any = None) -> dict[str, str]:
        captured.append(dict(params))
        return {"id": "email-id"}

    monkeypatch.setattr(resend.Emails, "send_async", fake_send)
    return captured


# -- The two bodies ------------------------------------------------------------------


def code_in_html(html: str) -> str:
    """The code as the body renders it — the only 6-digit run in its own element."""
    match = re.search(r'class="code"[^>]*>([0-9]{6})</span>', html)
    assert match is not None, html
    return match.group(1)


def test_expiry_text_follows_the_configured_lifetime() -> None:
    assert _expiry_text(59) == "59 seconds"
    assert _expiry_text(60) == "1 minute"
    assert _expiry_text(120) == "2 minutes"
    assert _expiry_text(300) == "5 minutes"


def test_both_purposes_render_their_own_wording() -> None:
    login = render_otp_email(purpose="login", code="123456", ttl_seconds=120)
    activation = render_otp_email(purpose="verify_contact", code="123456", ttl_seconds=120)

    assert "123456" in login and "123456" in activation
    assert "2 minutes" in login and "2 minutes" in activation
    assert _COPY["login"].heading in login
    assert _COPY["verify_contact"].heading in activation
    assert _COPY["login"].heading not in activation
    # One self-contained document: no external stylesheet, no images, no remote fonts.
    for body in (login, activation):
        assert body.startswith("<!DOCTYPE html>")
        assert (
            "<img" not in body and "<link" not in body and "http" not in body.split("</style>")[0]
        )


async def test_resend_activation_email_uses_its_own_subject_and_sender(
    client: AsyncClient, sent_params: list[dict[str, Any]]
) -> None:
    app.dependency_overrides[get_email_service] = lambda: EmailService(EMAIL_CONFIGURED_SETTINGS)
    headers, _user = await _headers(client)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert len(sent_params) == 1
    params = sent_params[0]
    assert params["from"] == "AI-chat@mail.erfancodes.ir"
    assert params["to"] == EMAIL
    # Confirming an address is not signing in: it must not arrive looking like a
    # sign-in code the user never asked for.
    assert params["subject"] == "Confirm your email address"
    assert _COPY["verify_contact"].heading in params["html"]
    assert _COPY["login"].heading not in params["html"]
    # The body is built here. The API never returns the code, so this is where it
    # is visible — and "template" may never be passed alongside "html".
    assert len(code_in_html(params["html"])) == 6
    assert "template" not in params
    assert resend.api_key == EMAIL_CONFIGURED_SETTINGS.resend_api_key


async def test_resend_login_email_differs_from_the_activation_one(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
    sent_params: list[dict[str, Any]],
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    # The account's second contact is an email, so a login can be sent by email.
    await _enable(client, headers, clock, email)
    await client.patch("/me", json={"preferred_otp_method": "EMAIL"}, headers=headers)

    # Only now swap the recording fake for the real service, so the login is the
    # send under test.
    app.dependency_overrides[get_email_service] = lambda: EmailService(EMAIL_CONFIGURED_SETTINGS)
    sent_params.clear()

    await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})

    assert len(sent_params) == 1
    params = sent_params[0]
    assert params["subject"] == "Your AI Chat verification code"
    assert _COPY["login"].heading in params["html"]
    assert _COPY["verify_contact"].heading not in params["html"]


async def test_resend_rate_limit_is_a_503_not_a_passthrough(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def limited(params: Any, options: Any = None) -> None:
        raise RateLimitError("Too many requests", "rate_limit_exceeded", "429")

    monkeypatch.setattr(resend.Emails, "send_async", limited)
    app.dependency_overrides[get_email_service] = lambda: EmailService(EMAIL_CONFIGURED_SETTINGS)
    headers, _user = await _headers(client)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Could not send the verification code. Please try again."}


async def test_resend_transport_failure_is_a_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(params: Any, options: Any = None) -> None:
        # What the SDK raises for a transport error: it wraps httpx failures.
        raise ResendError(500, "HttpClientError", "connection reset", "try again")

    monkeypatch.setattr(resend.Emails, "send_async", broken)
    app.dependency_overrides[get_email_service] = lambda: EmailService(EMAIL_CONFIGURED_SETTINGS)
    headers, _user = await _headers(client)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
    )

    assert response.status_code == 503


async def test_resend_empty_body_is_a_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``NoContentError`` is the one SDK failure outside the ResendError hierarchy."""

    async def empty(params: Any, options: Any = None) -> None:
        raise NoContentError()

    monkeypatch.setattr(resend.Emails, "send_async", empty)
    app.dependency_overrides[get_email_service] = lambda: EmailService(EMAIL_CONFIGURED_SETTINGS)
    headers, _user = await _headers(client)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
    )

    assert response.status_code == 503


async def test_unconfigured_service_never_reaches_the_sdk(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def must_not_run(params: Any, options: Any = None) -> None:
        raise AssertionError("the SDK was called without an API key")

    monkeypatch.setattr(resend.Emails, "send_async", must_not_run)
    app.dependency_overrides.pop(get_email_service, None)
    headers, _user = await _headers(client)

    response = await client.post(
        "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Email is not configured"}


async def test_logs_never_contain_the_code_or_the_recipient(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_send(params: Any, options: Any = None) -> dict[str, str]:
        captured.append(dict(params))
        return {"id": "email-id"}

    monkeypatch.setattr(resend.Emails, "send_async", fake_send)
    app.dependency_overrides[get_email_service] = lambda: EmailService(EMAIL_CONFIGURED_SETTINGS)
    headers, _user = await _headers(client)

    with caplog.at_level(logging.DEBUG):
        response = await client.post(
            "/me/otp/enable", json={"method": "EMAIL", "email": EMAIL}, headers=headers
        )
    assert response.status_code == 200

    code = code_in_html(captured[0]["html"])
    logs = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    assert code not in logs
    assert EMAIL_CONFIGURED_SETTINGS.resend_api_key not in logs
    # The address, though, is scoped to this application's own loggers. Looking a
    # recipient up by address necessarily puts it in the SQL statement, and
    # SQLAlchemy only echoes statements when a test (or an operator) turns that on —
    # this suite does not run with echo. What must never happen is the *app* writing
    # it, so that is what is asserted.
    app_logs = "\n".join(
        record.getMessage() + str(record.__dict__)
        for record in caplog.records
        if record.name.startswith("app.")
    )
    assert EMAIL not in app_logs


async def test_a_registered_account_starts_on_sms(client: AsyncClient) -> None:
    """Registration says nothing about channels: SMS is the inherited default."""
    await register_user(client, "bob")

    login = await login_user(client, "bob")

    assert login["user"]["preferred_otp_method"] == "SMS"
    assert login["user"]["email"] is None
