"""Administrator CRUD and privilege-escalation tests.

``ROLE_admin`` is granted only in the database (``promote_to_admin``) — no endpoint
writes USERS.ROLE — so these tests also prove authorization reads the stored role.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.character import Character
from app.db.models.user import USER_ROLE, User
from tests.conftest import auth_admin, auth_user, create_character, promote_to_admin

BUILTIN_CHARACTER_ID = 1
FORBIDDEN = {"detail": "Administrator privileges required"}


# -- Characters: admin CRUD ---------------------------------------------------------


async def test_admin_creates_global_character(client: AsyncClient, session_factory: Any) -> None:
    """An explicit null owner_user_id makes the character global (built-in)."""
    headers, _admin = await auth_admin(client, session_factory)

    character = await create_character(client, headers, name="Global Sage", owner_user_id=None)

    assert character["owner_user_id"] is None
    assert character["status"] == "ACTIVE"

    # Every user can now see it.
    user_headers, _user = await auth_user(client, "alice")
    response = await client.get("/characters", headers=user_headers)
    assert "Global Sage" in [c["name"] for c in response.json()]


async def test_admin_creates_character_for_another_user(
    client: AsyncClient, session_factory: Any
) -> None:
    headers_admin, _admin = await auth_admin(client, session_factory)
    headers_alice, alice = await auth_user(client, "alice")

    character = await create_character(
        client, headers_admin, name="Alice's Gift", owner_user_id=alice["id"]
    )
    assert character["owner_user_id"] == alice["id"]

    # It belongs to Alice, who can see it; Bob cannot.
    response = await client.get(f"/characters/{character['id']}", headers=headers_alice)
    assert response.status_code == 200
    headers_bob, _bob = await auth_user(client, "bob")
    response = await client.get(f"/characters/{character['id']}", headers=headers_bob)
    assert response.status_code == 404


async def test_admin_lists_and_reads_every_character(
    client: AsyncClient, session_factory: Any
) -> None:
    headers_admin, _admin = await auth_admin(client, session_factory)
    headers_alice, alice = await auth_user(client, "alice")
    headers_bob, bob = await auth_user(client, "bob")

    alice_character = await create_character(client, headers_alice, name="Alice's Nova")
    bob_character = await create_character(client, headers_bob, name="Bob's Rex")

    response = await client.get("/characters", headers=headers_admin)
    assert response.status_code == 200
    listed = response.json()
    assert [c["name"] for c in listed] == ["Maya", "Alice's Nova", "Bob's Rex"]
    assert [c["owner_user_id"] for c in listed] == [None, alice["id"], bob["id"]]

    # And can fetch either private character directly.
    for character_id in (alice_character["id"], bob_character["id"]):
        response = await client.get(f"/characters/{character_id}", headers=headers_admin)
        assert response.status_code == 200


async def test_admin_updates_any_character(client: AsyncClient, session_factory: Any) -> None:
    headers_admin, _admin = await auth_admin(client, session_factory)
    headers_alice, alice = await auth_user(client, "alice")
    character = await create_character(client, headers_alice, name="Alice's Nova")

    response = await client.patch(
        f"/characters/{character['id']}",
        json={
            "name": "Renamed by admin",
            "description": "Edited.",
            "avatar_url": "https://example.com/a.png",
            "system_prompt": "You are edited.",
        },
        headers=headers_admin,
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["name"] == "Renamed by admin"
    assert updated["description"] == "Edited."
    assert updated["avatar_url"] == "https://example.com/a.png"
    assert updated["system_prompt"] == "You are edited."
    assert updated["owner_user_id"] == alice["id"]  # ownership untouched
    assert updated["id"] == character["id"]
    assert updated["created_at"] == character["created_at"]  # immutable


async def test_admin_reassigns_ownership(client: AsyncClient, session_factory: Any) -> None:
    headers_admin, _admin = await auth_admin(client, session_factory)
    headers_alice, _alice = await auth_user(client, "alice")
    headers_bob, bob = await auth_user(client, "bob")
    character = await create_character(client, headers_alice, name="Handover")

    response = await client.patch(
        f"/characters/{character['id']}", json={"owner_user_id": bob["id"]}, headers=headers_admin
    )
    assert response.status_code == 200, response.text
    assert response.json()["owner_user_id"] == bob["id"]

    # Bob now sees it; Alice no longer does.
    assert (
        await client.get(f"/characters/{character['id']}", headers=headers_bob)
    ).status_code == 200
    assert (
        await client.get(f"/characters/{character['id']}", headers=headers_alice)
    ).status_code == 404


async def test_admin_reassign_to_unknown_user_is_rejected(
    client: AsyncClient, session_factory: Any
) -> None:
    headers_admin, _admin = await auth_admin(client, session_factory)
    headers_alice, _alice = await auth_user(client, "alice")
    character = await create_character(client, headers_alice, name="Nova")

    response = await client.patch(
        f"/characters/{character['id']}", json={"owner_user_id": 9999}, headers=headers_admin
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown owner user"}


async def test_admin_deletes_any_character(client: AsyncClient, session_factory: Any) -> None:
    headers_admin, _admin = await auth_admin(client, session_factory)
    headers_alice, _alice = await auth_user(client, "alice")
    character = await create_character(client, headers_alice, name="Doomed")

    response = await client.delete(f"/characters/{character['id']}", headers=headers_admin)
    assert response.status_code == 204

    # Soft delete: the row survives with STATUS='DELETED' and leaves the user's view.
    async with session_factory() as db:
        row = (await db.scalars(select(Character).where(Character.id == character["id"]))).first()
    assert row is not None
    assert row.status == "DELETED"

    response = await client.get(f"/characters/{character['id']}", headers=headers_alice)
    assert response.status_code == 404
    response = await client.get("/characters", headers=headers_alice)
    assert "Doomed" not in [c["name"] for c in response.json()]


async def test_admin_can_delete_and_restore_builtin_character(
    client: AsyncClient, session_factory: Any
) -> None:
    headers_admin, _admin = await auth_admin(client, session_factory)
    headers_alice, _alice = await auth_user(client, "alice")

    assert (
        await client.delete(f"/characters/{BUILTIN_CHARACTER_ID}", headers=headers_admin)
    ).status_code == 204
    assert (await client.get("/characters", headers=headers_alice)).json() == []

    # The admin still sees it (any status) and can bring it back.
    response = await client.get(f"/characters/{BUILTIN_CHARACTER_ID}", headers=headers_admin)
    assert response.status_code == 200
    assert response.json()["status"] == "DELETED"

    response = await client.patch(
        f"/characters/{BUILTIN_CHARACTER_ID}", json={"status": "ACTIVE"}, headers=headers_admin
    )
    assert response.status_code == 200
    assert [c["name"] for c in (await client.get("/characters", headers=headers_alice)).json()] == [
        "Maya"
    ]


async def test_admin_status_value_is_validated(client: AsyncClient, session_factory: Any) -> None:
    headers_admin, _admin = await auth_admin(client, session_factory)
    response = await client.patch(
        f"/characters/{BUILTIN_CHARACTER_ID}", json={"status": "ARCHIVED"}, headers=headers_admin
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Status must be one of: ACTIVE, DELETED"}


# -- Characters: regular users --------------------------------------------------------


async def test_regular_user_cannot_use_admin_fields(client: AsyncClient) -> None:
    """owner_user_id / status are 403 for a non-admin, never silently dropped."""
    headers_alice, _alice = await auth_user(client, "alice")
    _headers_bob, bob = await auth_user(client, "bob")

    response = await client.post(
        "/characters", json={"name": "Trojan", "owner_user_id": bob["id"]}, headers=headers_alice
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Administrator privileges required to set: owner_user_id"}

    response = await client.post(
        "/characters", json={"name": "Trojan", "status": "DELETED"}, headers=headers_alice
    )
    assert response.status_code == 403

    # Nothing was created.
    assert [c["name"] for c in (await client.get("/characters", headers=headers_alice)).json()] == [
        "Maya"
    ]


async def test_regular_user_cannot_reassign_ownership_of_own_character(
    client: AsyncClient,
) -> None:
    headers_alice, alice = await auth_user(client, "alice")
    _headers_bob, bob = await auth_user(client, "bob")
    character = await create_character(client, headers_alice, name="Mine")

    response = await client.patch(
        f"/characters/{character['id']}", json={"owner_user_id": bob["id"]}, headers=headers_alice
    )
    assert response.status_code == 403

    response = await client.get(f"/characters/{character['id']}", headers=headers_alice)
    assert response.json()["owner_user_id"] == alice["id"]


async def test_regular_user_cannot_touch_another_users_character(client: AsyncClient) -> None:
    headers_alice, _alice = await auth_user(client, "alice")
    headers_bob, _bob = await auth_user(client, "bob")
    bob_character = await create_character(client, headers_bob, name="Bob's Rex")

    # Invisible → 404, indistinguishable from nonexistent.
    for method, kwargs in (
        ("get", {}),
        ("patch", {"json": {"name": "stolen"}}),
        ("delete", {}),
    ):
        response = await getattr(client, method)(
            f"/characters/{bob_character['id']}", headers=headers_alice, **kwargs
        )
        assert response.status_code == 404, (method, response.text)

    # Bob's character is untouched.
    response = await client.get(f"/characters/{bob_character['id']}", headers=headers_bob)
    assert response.json()["name"] == "Bob's Rex"


async def test_regular_user_cannot_modify_builtin_character(client: AsyncClient) -> None:
    """Visible but not owned → 403 (403, not 404: the user can see it)."""
    headers, _user = await auth_user(client, "alice")

    response = await client.patch(
        f"/characters/{BUILTIN_CHARACTER_ID}", json={"name": "Mine now"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "You can only modify your own characters"}

    response = await client.delete(f"/characters/{BUILTIN_CHARACTER_ID}", headers=headers)
    assert response.status_code == 403


async def test_owner_updates_and_deletes_own_character(
    client: AsyncClient, session_factory: Any
) -> None:
    headers, user = await auth_user(client, "alice")
    character = await create_character(client, headers, name="Nova", description="First")

    response = await client.patch(
        f"/characters/{character['id']}",
        json={"name": "Nova II", "description": None},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Nova II"
    assert response.json()["description"] is None  # explicit null clears the column
    assert response.json()["owner_user_id"] == user["id"]

    response = await client.delete(f"/characters/{character['id']}", headers=headers)
    assert response.status_code == 204
    assert (await client.get(f"/characters/{character['id']}", headers=headers)).status_code == 404


async def test_character_update_requires_a_field(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")
    character = await create_character(client, headers, name="Nova")
    response = await client.patch(f"/characters/{character['id']}", json={}, headers=headers)
    assert response.status_code == 422


async def test_update_cannot_null_out_not_null_columns(
    client: AsyncClient, session_factory: Any
) -> None:
    """NAME/STATUS and the model's NOT NULL columns reject an explicit null (422)."""
    headers_admin, _admin = await auth_admin(client, session_factory)
    headers_alice, _alice = await auth_user(client, "alice")
    character = await create_character(client, headers_alice, name="Nova")

    for body in ({"name": None}, {"status": None}):
        response = await client.patch(
            f"/characters/{character['id']}", json=body, headers=headers_admin
        )
        assert response.status_code == 422, (body, response.text)

    for body in (
        {"model_name": None},
        {"provider_id": None},
        {"endpoint_id": None},
        {"active": None},
    ):
        response = await client.patch("/models/1", json=body, headers=headers_admin)
        assert response.status_code == 422, (body, response.text)

    # The rows survived intact.
    response = await client.get(f"/characters/{character['id']}", headers=headers_alice)
    assert response.json()["name"] == "Nova"
    response = await client.get("/models/1", headers=headers_admin)
    assert response.json()["model_name"] == "deepseek-v4-pro"
    assert response.json()["active"] is True


# -- Models: admin CRUD ---------------------------------------------------------------


async def test_admin_model_crud(client: AsyncClient, session_factory: Any) -> None:
    headers, _admin = await auth_admin(client, session_factory)

    # Create
    response = await client.post(
        "/models",
        json={
            "provider_id": 1,
            "endpoint_id": 1,
            "model_name": "deepseek-v4-flash",
            "display_name": "DeepSeek v4 Flash",
            "context_window": 8000,
            "active": True,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()
    model_id = created["id"]
    assert created["model_name"] == "deepseek-v4-flash"
    assert created["provider"] == "DeepSeek"
    assert created["active"] is True
    assert "api_key" not in created and "base_url" not in created  # secrets never exposed

    # Read (list + by id)
    response = await client.get("/models", headers=headers)
    assert model_id in [m["id"] for m in response.json()]
    response = await client.get(f"/models/{model_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["display_name"] == "DeepSeek v4 Flash"

    # Update
    response = await client.patch(
        f"/models/{model_id}",
        json={"display_name": "Flash", "context_window": 16000},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Flash"
    assert response.json()["context_window"] == 16000

    # Delete (deactivate)
    response = await client.delete(f"/models/{model_id}", headers=headers)
    assert response.status_code == 204
    response = await client.get(f"/models/{model_id}", headers=headers)
    assert response.json()["active"] is False


async def test_admin_sees_inactive_models_users_do_not(
    client: AsyncClient, session_factory: Any
) -> None:
    headers_admin, _admin = await auth_admin(client, session_factory)
    headers_user, _user = await auth_user(client, "alice")

    response = await client.post(
        "/models",
        json={"provider_id": 1, "endpoint_id": 1, "model_name": "hidden-model"},
        headers=headers_admin,
    )
    model_id = response.json()["id"]
    assert response.json()["active"] is False  # ACTIVE defaults to 0

    assert model_id in [
        m["id"] for m in (await client.get("/models", headers=headers_admin)).json()
    ]
    assert model_id not in [
        m["id"] for m in (await client.get("/models", headers=headers_user)).json()
    ]
    assert (await client.get(f"/models/{model_id}", headers=headers_user)).status_code == 404
    assert (await client.get(f"/models/{model_id}", headers=headers_admin)).status_code == 200


async def test_admin_model_validation(client: AsyncClient, session_factory: Any) -> None:
    headers, _admin = await auth_admin(client, session_factory)

    # Unknown foreign keys are 400, not a database error.
    response = await client.post(
        "/models", json={"provider_id": 99, "endpoint_id": 1, "model_name": "x"}, headers=headers
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown provider"}

    response = await client.post(
        "/models", json={"provider_id": 1, "endpoint_id": 99, "model_name": "x"}, headers=headers
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown endpoint"}

    # UNIQUE (provider_id, model_name, endpoint_id) is 409.
    response = await client.post(
        "/models",
        json={"provider_id": 1, "endpoint_id": 1, "model_name": "deepseek-v4-pro"},
        headers=headers,
    )
    assert response.status_code == 409

    # Unknown model → 404 for update and delete.
    assert (
        await client.patch("/models/9999", json={"display_name": "x"}, headers=headers)
    ).status_code == 404
    assert (await client.delete("/models/9999", headers=headers)).status_code == 404


# -- Models: regular users get 403 ----------------------------------------------------


async def test_regular_user_cannot_write_models(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")

    response = await client.post(
        "/models",
        json={"provider_id": 1, "endpoint_id": 1, "model_name": "sneaky"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json() == FORBIDDEN

    response = await client.patch("/models/1", json={"display_name": "Renamed"}, headers=headers)
    assert response.status_code == 403
    assert response.json() == FORBIDDEN

    response = await client.delete("/models/1", headers=headers)
    assert response.status_code == 403
    assert response.json() == FORBIDDEN

    # Model 1 is untouched and still active.
    response = await client.get("/models/1", headers=headers)
    assert response.json()["display_name"] == "DeepSeek v4 Pro"
    assert response.json()["active"] is True


async def test_admin_endpoints_reject_anonymous_callers(client: AsyncClient) -> None:
    """No token → 401 from the existing auth dependency, not 403."""
    for method, path, kwargs in (
        ("post", "/models", {"json": {"provider_id": 1, "endpoint_id": 1, "model_name": "x"}}),
        ("patch", "/models/1", {"json": {"display_name": "x"}}),
        ("delete", "/models/1", {}),
        ("patch", "/characters/1", {"json": {"name": "x"}}),
        ("delete", "/characters/1", {}),
    ):
        response = await getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)
        assert response.json() == {"detail": "Not authenticated"}


# -- Privilege escalation -------------------------------------------------------------


async def test_role_in_request_body_does_not_elevate(
    client: AsyncClient, session_factory: Any
) -> None:
    """Neither registration nor profile update can write USERS.ROLE."""
    response = await client.post(
        "/auth/register",
        json={
            "username": "climber",
            "password": "password123",
            "role": "ROLE_admin",
            "is_admin": True,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == USER_ROLE

    login = await client.post(
        "/auth/login", json={"username": "climber", "password": "password123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.patch(
        "/me",
        json={"display_name": "Climber", "role": "ROLE_admin", "is_admin": True},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["role"] == USER_ROLE

    async with session_factory() as db:
        row = (await db.scalars(select(User).where(User.username == "climber"))).first()
    assert row is not None
    assert row.role == USER_ROLE

    # Still no admin powers.
    response = await client.post(
        "/models",
        json={"provider_id": 1, "endpoint_id": 1, "model_name": "sneaky"},
        headers=headers,
    )
    assert response.status_code == 403


async def test_authorization_follows_the_stored_role(
    client: AsyncClient, session_factory: Any
) -> None:
    """The same token gains and loses admin power as the DB row changes."""
    headers, user = await auth_user(client, "alice")

    response = await client.post(
        "/models",
        json={"provider_id": 1, "endpoint_id": 1, "model_name": "later-admin"},
        headers=headers,
    )
    assert response.status_code == 403

    await promote_to_admin(session_factory, user["id"])

    response = await client.post(
        "/models",
        json={"provider_id": 1, "endpoint_id": 1, "model_name": "later-admin"},
        headers=headers,
    )
    assert response.status_code == 201, response.text

    # Demote: the same token is powerless again.
    async with session_factory() as db:
        row = (await db.scalars(select(User).where(User.id == user["id"]))).first()
        assert row is not None
        row.role = USER_ROLE
        await db.commit()

    response = await client.delete(f"/models/{response.json()['id']}", headers=headers)
    assert response.status_code == 403


async def test_role_comparison_is_exact_and_case_sensitive(
    client: AsyncClient, session_factory: Any
) -> None:
    headers, user = await auth_user(client, "alice")

    for near_miss in ("role_admin", "ROLE_ADMIN", "ROLE_admin ", "ROLE_administrator", "admin"):
        async with session_factory() as db:
            row = (await db.scalars(select(User).where(User.id == user["id"]))).first()
            assert row is not None
            row.role = near_miss
            await db.commit()

        response = await client.delete(f"/characters/{BUILTIN_CHARACTER_ID}", headers=headers)
        assert response.status_code == 403, near_miss
