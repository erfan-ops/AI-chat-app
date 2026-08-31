"""Authorization tests: user A must never reach user B's resources."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.conftest import auth_user, create_conversation, seed_conversation, seed_message


async def test_cross_user_conversation_access_denied(
    client: AsyncClient, session_factory: Any
) -> None:
    headers_a, _user_a = await auth_user(client, "alice")
    headers_b, _user_b = await auth_user(client, "bob")

    conversation = await create_conversation(client, headers_a)
    conversation_id = conversation["id"]
    await seed_message(
        session_factory, conversation_id=conversation_id, role="user", content="private"
    )

    # B cannot read, rename, or delete A's conversation.
    response = await client.get(f"/conversations/{conversation_id}", headers=headers_b)
    assert response.status_code == 404

    response = await client.patch(
        f"/conversations/{conversation_id}", json={"title": "stolen"}, headers=headers_b
    )
    assert response.status_code == 404

    response = await client.delete(f"/conversations/{conversation_id}", headers=headers_b)
    assert response.status_code == 404

    # B cannot read A's messages.
    response = await client.get(f"/conversations/{conversation_id}/messages", headers=headers_b)
    assert response.status_code == 404

    # B cannot send a message into A's conversation.
    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "hello?"},
        headers=headers_b,
    )
    assert response.status_code == 404

    # A still has full access.
    response = await client.get(f"/conversations/{conversation_id}", headers=headers_a)
    assert response.status_code == 200
    response = await client.get(f"/conversations/{conversation_id}/messages", headers=headers_a)
    assert response.status_code == 200
    assert [m["role"] for m in response.json()] == ["user"]

    # B's conversation list does not contain A's conversation.
    response = await client.get("/conversations", headers=headers_b)
    assert response.json() == []


async def test_cross_user_conversation_id_guessing(
    client: AsyncClient, session_factory: Any
) -> None:
    """Even a known conversation id is invisible to the wrong user."""
    _headers_a, user_a = await auth_user(client, "alice")
    headers_b, _user_b = await auth_user(client, "bob")
    conversation_id = await seed_conversation(session_factory, user_id=user_a["id"])

    response = await client.get(f"/conversations/{conversation_id}", headers=headers_b)
    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}
