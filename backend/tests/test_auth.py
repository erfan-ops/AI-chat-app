"""Authentication tests: register, login, validation, throttling, protected endpoints."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.conftest import TEST_PASSWORD, login_user, register_user


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
