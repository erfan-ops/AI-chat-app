"""The OTP history table: a code is recorded when issued, flagged when used.

The table is an audit trail around the flow, never a dependency of it — what a code
*does* is still decided by the in-process challenges in ``OtpService``.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models.otp_log import OtpLog
from app.db.repositories.otp_log import OtpLogRepository
from tests.conftest import (
    TEST_PASSWORD,
    FakeClock,
    RecordingEmailService,
    RecordingSmsService,
    auth_user,
)

MOBILE = "9123456789"
EMAIL = "ali@example.com"


async def _headers(client: AsyncClient, username: str = "alice") -> tuple[dict[str, str], dict]:
    headers, user = await auth_user(client, username)
    return headers, user


async def _rows(session_factory: Any) -> list[OtpLog]:
    async with session_factory() as db:
        return list((await db.scalars(select(OtpLog).order_by(OtpLog.id))).all())


async def _enable(
    client: AsyncClient,
    headers: dict[str, str],
    clock: FakeClock,
    fake: RecordingSmsService | RecordingEmailService,
    *,
    method: str = "SMS",
    destination: str = MOBILE,
) -> dict[str, Any]:
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


# -- Issuing -------------------------------------------------------------------------


async def test_issuing_an_enable_code_is_recorded(
    client: AsyncClient, sms: RecordingSmsService, session_factory: Any
) -> None:
    headers, user = await _headers(client)

    await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)

    rows = await _rows(session_factory)
    assert len(rows) == 1
    entry = rows[0]
    assert entry.user_id == user["id"]
    assert entry.purpose == "verify_contact"
    assert entry.method == "SMS"
    assert entry.consumed == 0
    assert entry.consumed_at is None
    assert entry.created_at is not None and entry.expires_at is not None
    assert entry.expires_at > entry.created_at


async def test_the_code_itself_is_never_stored(
    client: AsyncClient, sms: RecordingSmsService, session_factory: Any
) -> None:
    """Only the HMAC goes in — the table must not be a code store."""
    headers, _user = await _headers(client)
    await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)

    rows = await _rows(session_factory)
    entry = rows[0]
    code = sms.last_code
    assert entry.code_hash is not None
    assert code not in entry.code_hash
    assert len(entry.code_hash) == 64  # sha256 hex
    int(entry.code_hash, 16)  # it is hex, not a digest or a plaintext


async def test_login_code_is_recorded_as_a_login(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock, session_factory: Any
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms)

    await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})

    rows = await _rows(session_factory)
    assert [row.purpose for row in rows] == ["verify_contact", "login"]
    assert rows[-1].consumed == 0


async def test_a_failed_send_leaves_no_row(
    client: AsyncClient, sms: RecordingSmsService, session_factory: Any
) -> None:
    """Nothing was delivered, so there is no code to have a history of."""
    headers, _user = await _headers(client)
    sms.fail = True

    response = await client.post("/me/otp/enable", json={"mobile_number": MOBILE}, headers=headers)

    assert response.status_code == 503
    assert await _rows(session_factory) == []


# -- Consuming -----------------------------------------------------------------------


async def test_verifying_marks_the_row_consumed(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock, session_factory: Any
) -> None:
    headers, _user = await _headers(client)

    await _enable(client, headers, clock, sms)

    rows = await _rows(session_factory)
    assert len(rows) == 1
    assert rows[0].consumed == 1
    assert rows[0].consumed_at is not None


async def test_completing_a_login_marks_the_login_row_consumed(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock, session_factory: Any
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    response = await client.post(
        "/auth/login/otp",
        json={"challenge_id": challenge.json()["challenge_id"], "code": sms.last_code},
    )

    assert response.status_code == 200, response.text
    rows = await _rows(session_factory)
    login_row = rows[-1]
    assert login_row.purpose == "login"
    assert login_row.consumed == 1
    assert login_row.consumed_at is not None
    # The enable row from earlier keeps its own history.
    assert rows[0].consumed == 1


async def test_a_superseded_code_is_left_unconsumed(
    client: AsyncClient,
    email: RecordingEmailService,
    sms: RecordingSmsService,
    clock: FakeClock,
    session_factory: Any,
) -> None:
    """Switching method replaces the code; the old row was issued and never used."""
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms, method="SMS", destination=MOBILE)
    await _enable(client, headers, clock, email, method="EMAIL", destination=EMAIL)
    await client.patch("/me", json={"preferred_otp_method": "SMS"}, headers=headers)
    first = await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})

    switched = await client.post(
        "/auth/login/otp/method",
        json={"challenge_id": first.json()["challenge_id"], "method": "EMAIL"},
    )
    await client.post(
        "/auth/login/otp",
        json={"challenge_id": switched.json()["challenge_id"], "code": email.last_code},
    )

    login_rows = [row for row in await _rows(session_factory) if row.purpose == "login"]
    assert [row.method for row in login_rows] == ["SMS", "EMAIL"]
    # The abandoned SMS code stays at 0: it was issued, it expired unused. Only the
    # code the user actually typed is flagged.
    assert login_rows[0].consumed == 0
    assert login_rows[0].consumed_at is None
    assert login_rows[1].consumed == 1


async def test_wrong_code_consumes_nothing(
    client: AsyncClient, sms: RecordingSmsService, clock: FakeClock, session_factory: Any
) -> None:
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    response = await client.post(
        "/auth/login/otp",
        json={"challenge_id": challenge.json()["challenge_id"], "code": "000000"},
    )

    assert response.status_code == 400
    login_rows = [row for row in await _rows(session_factory) if row.purpose == "login"]
    assert login_rows[0].consumed == 0


# -- The history is never load-bearing ------------------------------------------------


async def test_a_failing_audit_write_does_not_break_the_flow(
    client: AsyncClient,
    sms: RecordingSmsService,
    clock: FakeClock,
    session_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full or broken audit table must not cost anyone a login."""

    async def broken_add(self: OtpLogRepository, **kwargs: Any) -> Any:
        raise SQLAlchemyError("OTP_LOG is unavailable")

    monkeypatch.setattr(OtpLogRepository, "add", broken_add)
    headers, _user = await _headers(client)

    # The whole flow still works, and nothing was recorded.
    profile = await _enable(client, headers, clock, sms)

    assert profile["otp_enabled"] is True
    assert await _rows(session_factory) == []


async def test_a_failing_consume_write_does_not_break_the_login(
    client: AsyncClient,
    sms: RecordingSmsService,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_mark(self: OtpLogRepository, **kwargs: Any) -> Any:
        raise SQLAlchemyError("OTP_LOG is unavailable")

    monkeypatch.setattr(OtpLogRepository, "mark_consumed", broken_mark)
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, sms)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    response = await client.post(
        "/auth/login/otp",
        json={"challenge_id": challenge.json()["challenge_id"], "code": sms.last_code},
    )

    assert response.status_code == 200, response.text
    assert "access_token" in response.json()


async def test_consuming_with_no_matching_row_is_harmless(
    client: AsyncClient, email: RecordingEmailService, clock: FakeClock
) -> None:
    """A code issued before this table existed still verifies."""
    headers, _user = await _headers(client)
    await _enable(client, headers, clock, email, method="EMAIL", destination=EMAIL)
    challenge = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )

    response = await client.post(
        "/auth/login/otp",
        json={"challenge_id": challenge.json()["challenge_id"], "code": email.last_code},
    )

    assert response.status_code == 200, response.text
