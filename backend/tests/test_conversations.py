"""Conversation lifecycle tests."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.core.time import utcnow
from app.db.models.conversation import Conversation
from tests.conftest import auth_user, create_conversation


async def test_create_conversation_defaults(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    assert conversation["title"] == "Chat with Maya"
    assert conversation["status"] == "ACTIVE"
    assert conversation["character"]["id"] == 1
    assert conversation["character"]["name"] == "Maya"
    assert conversation["model"]["id"] == 1  # first active model
    assert conversation["created_at"] is not None


async def test_create_conversation_with_explicit_model_and_title(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers, model_id=1, title="Us")
    assert conversation["model"]["id"] == 1
    assert conversation["title"] == "Us"


async def test_create_conversation_unknown_model(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")
    response = await client.post(
        "/conversations", json={"character_id": 1, "model_id": 999}, headers=headers
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown or inactive model"}


async def test_create_conversation_unknown_character(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")
    response = await client.post("/conversations", json={"character_id": 999}, headers=headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Character not found"}


async def test_list_conversations_most_recent_first(
    client: AsyncClient, session_factory: Any
) -> None:
    headers, _user = await auth_user(client, "alice")
    first = await create_conversation(client, headers, title="First")
    await create_conversation(client, headers, title="Second")

    # Touch the first conversation so it becomes the most recent.
    async with session_factory() as db:
        conversation = (
            await db.scalars(select(Conversation).where(Conversation.id == first["id"]))
        ).first()
        assert conversation is not None
        conversation.last_message_at = utcnow()
        await db.commit()

    response = await client.get("/conversations", headers=headers)
    assert response.status_code == 200
    titles = [c["title"] for c in response.json()]
    assert titles == ["First", "Second"]

    # Pagination bounds.
    response = await client.get("/conversations?limit=1&offset=1", headers=headers)
    assert [c["title"] for c in response.json()] == ["Second"]


async def test_get_conversation(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers, title="Mine")
    response = await client.get(f"/conversations/{conversation['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["title"] == "Mine"

    response = await client.get("/conversations/999", headers=headers)
    assert response.status_code == 404


async def test_update_conversation_title(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    response = await client.patch(
        f"/conversations/{conversation['id']}", json={"title": "Renamed"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"

    response = await client.patch(
        f"/conversations/{conversation['id']}", json={"title": ""}, headers=headers
    )
    assert response.status_code == 422


async def test_delete_conversation_is_soft(client: AsyncClient, session_factory: Any) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]

    response = await client.delete(f"/conversations/{conversation_id}", headers=headers)
    assert response.status_code == 204

    # Gone from the API...
    response = await client.get(f"/conversations/{conversation_id}", headers=headers)
    assert response.status_code == 404
    response = await client.get("/conversations", headers=headers)
    assert response.json() == []

    # ...but preserved in the database with STATUS='DELETED'.
    async with session_factory() as db:
        row = (
            await db.scalars(select(Conversation).where(Conversation.id == conversation_id))
        ).first()
    assert row is not None
    assert row.status == "DELETED"


async def test_deleting_unknown_conversation_returns_404(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")
    response = await client.delete("/conversations/999", headers=headers)
    assert response.status_code == 404
