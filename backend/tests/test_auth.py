"""Authentication tests: register, login, validation, throttling, protected endpoints."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.conftest import TEST_PASSWORD, auth_user, login_user, register_user


async def test_register_success(client: AsyncClient) -> None:
    user = await register_user(client, "alice", display_name="Alice")
    assert user["username"] == "alice"
    assert user["display_name"] == "Alice"
    assert user["status"] == "ACTIVE"
    assert user["role"] == "ROLE_user"
    assert "password_hash" not in user


async def test_register_duplicate_username_rejected(client: AsyncClient) -> None:
    await register_user(client, "alice")
    response = await client.post(
        "/auth/register", json={"username": "alice", "password": TEST_PASSWORD}
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "Username is already taken"}


async def test_register_validation(client: AsyncClient) -> None:
    bad_bodies: list[dict[str, Any]] = [
        {"username": "ab", "password": TEST_PASSWORD},  # too short
        {"username": "bad name!", "password": TEST_PASSWORD},  # illegal chars
        {"username": "valid_name", "password": "short"},  # password too short
        {"password": TEST_PASSWORD},  # missing username
    ]
    for body in bad_bodies:
        response = await client.post("/auth/register", json=body)
        assert response.status_code == 422, (body, response.text)


async def test_login_success_returns_working_token(client: AsyncClient) -> None:
    await register_user(client, "alice")
    login = await login_user(client, "alice")
    assert login["token_type"] == "bearer"
    assert login["expires_in"] > 0
    assert login["user"]["username"] == "alice"

    me = await client.get("/me", headers={"Authorization": f"Bearer {login['access_token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


async def test_login_wrong_password_rejected(client: AsyncClient) -> None:
    await register_user(client, "alice")
    response = await client.post(
        "/auth/login", json={"username": "alice", "password": "wrong-password"}
    )
    assert response.status_code == 401


async def test_login_unknown_user_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"username": "nobody", "password": TEST_PASSWORD}
    )
    assert response.status_code == 401


async def test_login_throttled_after_repeated_failures(client: AsyncClient) -> None:
    await register_user(client, "alice")
    for _ in range(5):
        response = await client.post(
            "/auth/login", json={"username": "alice", "password": "wrong-password"}
        )
        assert response.status_code == 401
    # Locked now — even the correct password is rejected with 429.
    response = await client.post(
        "/auth/login", json={"username": "alice", "password": TEST_PASSWORD}
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers


async def test_protected_endpoints_reject_anonymous(client: AsyncClient) -> None:
    for method, path in [
        ("GET", "/me"),
        ("GET", "/conversations"),
        ("GET", "/characters"),
        ("GET", "/models"),
        ("GET", "/memories"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == 401, (method, path, response.text)
        assert response.json() == {"detail": "Not authenticated"}


async def test_protected_endpoints_reject_garbage_token(client: AsyncClient) -> None:
    response = await client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


async def test_password_stored_hashed(client: AsyncClient, session_factory: Any) -> None:
    """The DB must never contain the plaintext password."""
    await register_user(client, "alice")
    from sqlalchemy import select

    from app.db.models.user import User

    async with session_factory() as db:
        user = (await db.scalars(select(User).where(User.username == "alice"))).first()
    assert user is not None
    assert user.password_hash != TEST_PASSWORD
    assert user.password_hash.startswith("$argon2")


# -- Changing the password ------------------------------------------------------------


async def test_change_password_replaces_the_credential(client: AsyncClient) -> None:
    """Old password stops working, new one signs in."""
    headers, _user = await auth_user(client, "alice")

    response = await client.post(
        "/me/password",
        json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-password"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["username"] == "alice"
    assert "password_hash" not in response.json()

    old = await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    new = await client.post(
        "/auth/login", json={"username": "alice", "password": "a-brand-new-password"}
    )
    assert old.status_code == 401
    assert new.status_code == 200


async def test_wrong_current_password_is_400_not_401(client: AsyncClient) -> None:
    """401 here would sign the user out of the session they are changing from."""
    headers, _user = await auth_user(client, "alice")

    response = await client.post(
        "/me/password",
        json={"current_password": "not-the-password", "new_password": "another-password"},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "That password is incorrect"}
    # Still signed in, and the password is unchanged.
    assert (await client.get("/me", headers=headers)).status_code == 200
    assert (
        await client.post("/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    ).status_code == 200


async def test_change_password_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(
        "/me/password",
        json={"current_password": TEST_PASSWORD, "new_password": "another-password"},
    )

    assert response.status_code == 401


async def test_new_password_must_meet_the_registration_rules(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")

    response = await client.post(
        "/me/password",
        json={"current_password": TEST_PASSWORD, "new_password": "short"},
        headers=headers,
    )

    assert response.status_code == 422


async def test_repeated_wrong_current_passwords_are_throttled(client: AsyncClient) -> None:
    """Same counter as login: a stolen token must not be a guessing oracle."""
    headers, _user = await auth_user(client, "alice")

    for _ in range(5):
        attempt = await client.post(
            "/me/password",
            json={"current_password": "guess", "new_password": "another-password"},
            headers=headers,
        )
        assert attempt.status_code == 400

    blocked = await client.post(
        "/me/password",
        json={"current_password": TEST_PASSWORD, "new_password": "another-password"},
        headers=headers,
    )

    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


async def test_change_password_keeps_existing_tokens_working(client: AsyncClient) -> None:
    """No revocation in this codebase: the new password applies at the next sign-in."""
    headers, _user = await auth_user(client, "alice")

    await client.post(
        "/me/password",
        json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-password"},
        headers=headers,
    )

    assert (await client.get("/me", headers=headers)).status_code == 200


async def test_neither_password_is_ever_logged(client: AsyncClient, caplog: Any) -> None:
    import logging

    headers, _user = await auth_user(client, "alice")
    with caplog.at_level(logging.DEBUG):
        await client.post(
            "/me/password",
            json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-password"},
            headers=headers,
        )

    logs = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    assert TEST_PASSWORD not in logs
    assert "a-brand-new-password" not in logs
