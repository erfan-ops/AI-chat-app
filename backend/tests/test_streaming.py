"""Streaming tests: SSE event flow, incremental delivery, persistence, errors,
and client-disconnect handling. The AI provider is always scripted — no real
external provider is ever contacted."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.ai.base import DeltaEvent, ErrorEvent, StreamEvent
from app.db.models.message import Message, MessageGeneration
from app.services.ai_service import AIService
from tests.conftest import (
    DEFAULT_CHUNKS,
    DEFAULT_REPLY,
    TEST_SETTINGS,
    ScriptedProvider,
    auth_user,
    create_conversation,
    make_request,
    parse_sse,
    seed_conversation,
    seed_message,
)


async def test_stream_success_flow(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Hi, how are you?"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    # Note: ASGITransport collapses send-chunks into one body part, so wire-level
    # framing is asserted in test_stream_consumes_provider_incrementally instead.
    body = (await response.aread()).decode()
    events = parse_sse(body)

    names = [name for name, _data in events]
    assert names[0] == "message.created"
    assert "error" not in names
    assert names[-1] == "message.completed"

    # The created event carries the persisted user message.
    created = events[0][1]["message"]
    assert created["role"] == "user"
    assert created["content"] == "Hi, how are you?"
    assert created["conversation_id"] == conversation_id

    # Deltas concatenate to the full reply, in order.
    deltas = [data["content"] for name, data in events if name == "message.delta"]
    assert deltas == DEFAULT_CHUNKS
    assert "".join(deltas) == DEFAULT_REPLY

    # Completion carries the persisted assistant message + usage.
    completed = events[-1][1]
    assert completed["message"]["role"] == "assistant"
    assert completed["message"]["content"] == DEFAULT_REPLY
    assert completed["usage"] == {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}
    assert completed["latency_ms"] >= 0

    # Both messages and the generation telemetry are persisted.
    async with session_factory() as db:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        ).all()
        assert [(m.role, m.content) for m in messages] == [
            ("user", "Hi, how are you?"),
            ("assistant", DEFAULT_REPLY),
        ]
        generations = (await db.scalars(select(MessageGeneration))).all()
        assert len(generations) == 1
        generation = generations[0]
        assert generation.message_id == messages[1].id
        assert generation.model_id == 1
        assert generation.purpose == "chat"
        assert generation.input_tokens == 12
        assert generation.output_tokens == 5
        assert generation.total_tokens == 17
        assert generation.latency_ms is not None

    # The provider request must carry the persona system prompt and history.
    request = scripted_provider.requests[0]
    assert request.messages[0].role == "system"
    assert "Maya" in request.messages[0].content
    assert request.messages[-1].role == "user"
    assert request.messages[-1].content == "Hi, how are you?"
    # No replies in this conversation → the system prompt stays unchanged.
    assert "reply_context" not in request.messages[0].content


async def test_stream_consumes_provider_incrementally(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """Prove the API forwards chunks as the provider yields them (no full buffering)."""
    headers, user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)

    service = AIService(TEST_SETTINGS)
    prepared = await service.prepare_message(
        conversation_id=conversation["id"],
        user_id=user["id"],
        content="hi",
        session_factory=session_factory,
    )
    stream = service.stream_response(
        prepared,
        session_factory=session_factory,
        provider_factory=lambda _endpoint, _timeout: scripted_provider,
        request=make_request(),
    )

    created = await anext(stream)  # message.created — no provider consumption yet
    assert len(scripted_provider.yielded) == 0
    assert "event: message.created" in created

    first_delta = await anext(stream)  # exactly one provider event consumed
    assert len(scripted_provider.yielded) == 1
    assert isinstance(scripted_provider.yielded[0], DeltaEvent)
    assert f'"content":"{DEFAULT_CHUNKS[0]}"' in first_delta
    assert "event: message.delta" in first_delta

    second_delta = await anext(stream)
    assert len(scripted_provider.yielded) == 2
    assert f'"content":"{DEFAULT_CHUNKS[1]}"' in second_delta
    # Each yield carries exactly one SSE event — deltas are flushed as they arrive.
    assert first_delta.rstrip("\n").endswith('"}')
    assert "\n\n" not in first_delta.strip()
    # Drain the rest so the response persists (and resources are released).
    async for _event in stream:
        pass


async def test_stream_provider_error_persists_only_user_message(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]

    scripted_provider.events = [DeltaEvent(content="partial"), ErrorEvent(message="boom")]

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "hi"},
        headers=headers,
    )
    body = (await response.aread()).decode()
    events = parse_sse(body)
    names = [name for name, _data in events]
    assert names == ["message.created", "message.delta", "error"]
    assert events[-1][1]["code"] == "provider_error"

    # The user message was persisted; nothing partial for the assistant.
    async with session_factory() as db:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        ).all()
        assert [m.role for m in messages] == ["user"]
        assert (await db.scalars(select(MessageGeneration))).all() == []


async def test_stream_unexpected_provider_exception_yields_error_event(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """Provider crashes mid-stream: the client gets an error event, not a hang."""

    class CrashingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def stream_chat(self, _request: Any) -> AsyncGenerator[StreamEvent]:
            self.calls += 1
            if self.calls > 100:  # pragma: no cover — keeps this an async generator
                yield DeltaEvent(content="")
            raise RuntimeError("kaboom")

    crashing = CrashingProvider()
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)

    def crashing_factory(_endpoint: Any, _timeout: Any) -> CrashingProvider:
        return crashing

    service = AIService(TEST_SETTINGS)
    prepared = await service.prepare_message(
        conversation_id=conversation["id"],
        user_id=_user["id"],
        content="hi",
        session_factory=session_factory,
    )
    stream = service.stream_response(
        prepared,
        session_factory=session_factory,
        provider_factory=crashing_factory,
        request=make_request(),
    )
    chunks = [chunk async for chunk in stream]
    events = parse_sse("".join(chunks))
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "provider_error"


async def test_stream_client_disconnect_cancels_provider_and_persists_nothing(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    headers, user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]

    service = AIService(TEST_SETTINGS)
    prepared = await service.prepare_message(
        conversation_id=conversation_id,
        user_id=user["id"],
        content="hi",
        session_factory=session_factory,
    )
    stream = service.stream_response(
        prepared,
        session_factory=session_factory,
        provider_factory=lambda _endpoint, _timeout: scripted_provider,
        request=make_request(),
    )
    await anext(stream)  # message.created
    await anext(stream)  # first delta
    await stream.aclose()  # client goes away

    # The provider stream was closed and nothing partial was persisted.
    assert scripted_provider.closed is True
    async with session_factory() as db:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        ).all()
        assert [m.role for m in messages] == ["user"]
        assert (await db.scalars(select(MessageGeneration))).all() == []


async def test_stream_message_to_unknown_conversation_returns_404(
    client: AsyncClient, session_factory: Any
) -> None:
    """Pre-flight failures are real HTTP errors, not SSE."""
    headers, _user = await auth_user(client, "alice")
    unknown_id = await seed_conversation(session_factory, user_id=999)  # owned by nobody
    response = await client.post(
        f"/conversations/{unknown_id}/messages", json={"content": "hi"}, headers=headers
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}


async def test_stream_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/conversations/1/messages", json={"content": "hi"})
    assert response.status_code == 401


async def test_stream_reply_persists_reply_to_id(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """A message sent with reply_to_id carries the reference on the user message only."""
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]
    target_id = await seed_message(
        session_factory,
        conversation_id=conversation_id,
        role="assistant",
        content="What do you dream about?",
    )

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Flying over the ocean", "reply_to_id": target_id},
        headers=headers,
    )
    assert response.status_code == 200
    events = parse_sse((await response.aread()).decode())

    # The persisted user message carries the reply reference; the assistant reply
    # is a fresh message and does not inherit it.
    created = events[0][1]["message"]
    assert created["reply_to_id"] == target_id
    completed = events[-1][1]["message"]
    assert completed["role"] == "assistant"
    assert completed["reply_to_id"] is None

    async with session_factory() as db:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        ).all()
        assert [(m.role, m.reply_to_id) for m in messages] == [
            ("assistant", None),
            ("user", target_id),
            ("assistant", None),
        ]


async def test_stream_reply_without_reply_to_id_defaults_to_null(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)

    response = await client.post(
        f"/conversations/{conversation['id']}/messages",
        json={"content": "hi"},
        headers=headers,
    )
    assert response.status_code == 200
    created = parse_sse((await response.aread()).decode())[0][1]["message"]
    assert created["reply_to_id"] is None


async def test_stream_reply_to_message_in_other_conversation_returns_404(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """A reply target from another conversation is rejected like a missing one."""
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    other_conversation_id = await seed_conversation(session_factory, user_id=_user["id"])
    foreign_id = await seed_message(
        session_factory,
        conversation_id=other_conversation_id,
        role="assistant",
        content="secret",
    )

    response = await client.post(
        f"/conversations/{conversation['id']}/messages",
        json={"content": "hi", "reply_to_id": foreign_id},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Reply target not found"}


async def test_stream_reply_to_missing_message_returns_404(
    client: AsyncClient, scripted_provider: ScriptedProvider
) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)

    response = await client.post(
        f"/conversations/{conversation['id']}/messages",
        json={"content": "hi", "reply_to_id": 999_999},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Reply target not found"}


# -- Reply context in the AI request ------------------------------------------------


async def test_stream_reply_sends_reply_context_to_provider(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """Replying to an assistant message quotes it in the AI request, role intact."""
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]
    target_id = await seed_message(
        session_factory,
        conversation_id=conversation_id,
        role="assistant",
        content="You should get some rest.",
    )

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Why?", "reply_to_id": target_id},
        headers=headers,
    )
    assert response.status_code == 200
    await response.aread()  # drain the stream so persistence completes

    request = scripted_provider.requests[-1]
    # The message keeps its real role; only the content is augmented.
    last = request.messages[-1]
    assert last.role == "user"
    assert last.content == (
        '<reply_context>\n<message sender="assistant">\nYou should get some rest.\n'
        "</message>\n</reply_context>\n\nWhy?"
    )
    # The quoted message is not duplicated in history, and no DB id leaks.
    assistant_messages = [m for m in request.messages if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].content == "You should get some rest."
    assert str(target_id) not in last.content
    # The system prompt explains the format because the conversation uses replies.
    assert "`<reply_context>` block" in request.messages[0].content


async def test_stream_reply_to_user_message_uses_user_sender(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]
    target_id = await seed_message(
        session_factory,
        conversation_id=conversation_id,
        role="user",
        content="I'm feeling tired today.",
    )

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Why?", "reply_to_id": target_id},
        headers=headers,
    )
    assert response.status_code == 200
    await response.aread()

    request = scripted_provider.requests[-1]
    assert request.messages[-1].content == (
        '<reply_context>\n<message sender="user">\nI\'m feeling tired today.\n'
        "</message>\n</reply_context>\n\nWhy?"
    )


async def test_stream_reply_context_missing_target_is_skipped(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """A history reply whose target was deleted degrades to a plain message."""
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]
    target_id = await seed_message(
        session_factory,
        conversation_id=conversation_id,
        role="assistant",
        content="You should get some rest.",
    )
    first = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Why?", "reply_to_id": target_id},
        headers=headers,
    )
    assert first.status_code == 200
    await first.aread()

    # Remove the referenced message directly from the database.
    async with session_factory() as db:
        target = await db.get(Message, target_id)
        assert target is not None
        await db.delete(target)
        await db.commit()

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Second message"},
        headers=headers,
    )
    assert response.status_code == 200
    await response.aread()

    request = scripted_provider.requests[-1]
    # Neither the new message nor the earlier reply is quoted — no crash, no block.
    assert request.messages[-1].content == "Second message"
    assert any(m.content == "Why?" for m in request.messages)
    assert not any("reply_context" in m.content for m in request.messages[1:])
    assert "`<reply_context>` block" not in request.messages[0].content
