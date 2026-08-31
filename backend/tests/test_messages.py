"""Message listing and ownership tests (the streaming path is covered in
test_streaming.py)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.conftest import auth_user, create_conversation, seed_message


async def test_list_messages_empty(client: AsyncClient) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    response = await client.get(f"/conversations/{conversation['id']}/messages", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


async def test_list_messages_chronological(client: AsyncClient, session_factory: Any) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]
    await seed_message(session_factory, conversation_id=conversation_id, role="user", content="one")
    await seed_message(
        session_factory, conversation_id=conversation_id, role="assistant", content="two"
    )
    await seed_message(
        session_factory, conversation_id=conversation_id, role="user", content="three"
    )

    response = await client.get(f"/conversations/{conversation_id}/messages", headers=headers)
    messages = response.json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "one"),
        ("assistant", "two"),
        ("user", "three"),
    ]


async def test_list_messages_pagination_before_id(
    client: AsyncClient, session_factory: Any
) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]
    first = await seed_message(
        session_factory, conversation_id=conversation_id, role="user", content="one"
    )
    await seed_message(session_factory, conversation_id=conversation_id, role="user", content="two")
    await seed_message(
        session_factory, conversation_id=conversation_id, role="user", content="three"
    )

    # before_id is exclusive and returns older messages.
    response = await client.get(
        f"/conversations/{conversation_id}/messages?before_id={first + 1}", headers=headers
    )
    assert [m["content"] for m in response.json()] == ["one"]

    response = await client.get(
        f"/conversations/{conversation_id}/messages?limit=1", headers=headers
    )
    # Limit returns the NEWEST messages (page backwards with before_id).
    assert [m["content"] for m in response.json()] == ["three"]


async def test_list_messages_rejects_other_users_conversation(
    client: AsyncClient, session_factory: Any
) -> None:
    headers_a, _user_a = await auth_user(client, "alice")
    headers_b, _user_b = await auth_user(client, "bob")
    conversation = await create_conversation(client, headers_a)
    await seed_message(
        session_factory, conversation_id=conversation["id"], role="user", content="secret"
    )

    response = await client.get(f"/conversations/{conversation['id']}/messages", headers=headers_b)
    assert response.status_code == 404
