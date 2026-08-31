"""Streaming tests: SSE event flow, incremental delivery, persistence, errors,
and client-disconnect handling. The AI provider is always scripted — no real
external provider is ever contacted."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.ai.base import CompletionEvent, DeltaEvent, ErrorEvent, StreamEvent, Usage
from app.ai.context import MESSAGE_FORMAT_HINT
from app.db.models.message import Message, MessageGeneration
from app.services.ai_service import AIService
from tests.conftest import (
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

    # Deltas are the extracted content, in order — never the raw JSON envelope.
    deltas = [data["content"] for name, data in events if name == "message.delta"]
    assert deltas == ["Hello", ", I was thinking", " about you."]
    assert "".join(deltas) == DEFAULT_REPLY
    assert all('"content"' not in d for d in deltas)

    # Completion carries the persisted assistant message + usage.
    completed = events[-1][1]
    assert completed["message"]["role"] == "assistant"
    assert completed["message"]["content"] == DEFAULT_REPLY
    assert completed["message"]["reply_to_id"] is None
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
    assert MESSAGE_FORMAT_HINT in request.messages[0].content
    # History messages reach the provider as JSON envelopes with their ids.
    envelope = json.loads(request.messages[-1].content)
    assert envelope == {
        "id": created["id"],
        "role": "user",
        "content": "Hi, how are you?",
        "reply_to_id": None,
    }


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
    # The delta carries the extracted content — the raw JSON envelope never leaks.
    assert '"content":"Hello"' in first_delta
    assert '"{"content"' not in first_delta
    assert "event: message.delta" in first_delta

    second_delta = await anext(stream)
    assert len(scripted_provider.yielded) == 2
    assert '"content":", I was thinking"' in second_delta
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

    scripted_provider.events = [
        DeltaEvent(content='{"content": "partial'),
        ErrorEvent(message="boom"),
    ]

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


# -- Structured messages in the AI request -------------------------------------------


async def test_stream_reply_envelopes_history_messages_with_ids(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """Reply messages carry id/role/content/reply_to_id in the AI request, and
    assistant history messages are enveloped too — the AI sees every id."""
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
    events = parse_sse((await response.aread()).decode())
    created_id = events[0][1]["message"]["id"]

    request = scripted_provider.requests[-1]
    # The new message keeps its real role; its envelope carries the reply ref.
    last = request.messages[-1]
    assert last.role == "user"
    assert json.loads(last.content) == {
        "id": created_id,
        "role": "user",
        "content": "Why?",
        "reply_to_id": target_id,
    }
    # The assistant target is enveloped too — the AI sees its id and content.
    assistant_messages = [m for m in request.messages if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assert json.loads(assistant_messages[0].content) == {
        "id": target_id,
        "role": "assistant",
        "content": "You should get some rest.",
        "reply_to_id": None,
    }
    # The system prompt explains the envelope contract unconditionally.
    assert MESSAGE_FORMAT_HINT in request.messages[0].content


async def test_stream_reply_to_user_message_keeps_user_role(
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
    target = json.loads(request.messages[-2].content)
    assert target["role"] == "user"
    assert target["id"] == target_id
    assert target["content"] == "I'm feeling tired today."
    assert json.loads(request.messages[-1].content)["reply_to_id"] == target_id


async def test_stream_reply_to_deleted_message_degrades_safely(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """A history reply whose target was deleted still reaches the AI as a number
    — the envelope is self-describing, no lookup pass, no crash."""
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
    # The new message is plain; the older reply keeps its stale reference.
    assert json.loads(request.messages[-1].content)["reply_to_id"] is None
    assert any(
        json.loads(m.content)["reply_to_id"] == target_id
        for m in request.messages[1:]
        if m.role == "user"
    )
    # No XML leftovers anywhere.
    assert not any("reply_context" in m.content for m in request.messages)


# -- The AI's structured reply ---------------------------------------------------------


async def test_stream_ai_reply_to_id_is_persisted(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """The AI can reply to an earlier message: its reply_to_id is persisted and
    surfaced on message.completed."""
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]
    target_id = await seed_message(
        session_factory,
        conversation_id=conversation_id,
        role="assistant",
        content="You should get some rest.",
    )
    raw = json.dumps({"content": "Good question!", "reply_to_id": target_id})
    scripted_provider.events = [
        DeltaEvent(content=raw[:18]),
        DeltaEvent(content=raw[18:]),
        CompletionEvent(content=raw, usage=Usage(input_tokens=8, output_tokens=3)),
    ]

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Why?"},
        headers=headers,
    )
    assert response.status_code == 200
    events = parse_sse((await response.aread()).decode())

    completed = events[-1][1]
    assert completed["message"]["content"] == "Good question!"
    assert completed["message"]["reply_to_id"] == target_id
    # Deltas carried the clean content — never the raw envelope.
    deltas = [data["content"] for name, data in events if name == "message.delta"]
    assert "".join(deltas) == "Good question!"

    async with session_factory() as db:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        ).all()
        assert [(m.role, m.content, m.reply_to_id) for m in messages] == [
            ("assistant", "You should get some rest.", None),
            ("user", "Why?", None),
            ("assistant", "Good question!", target_id),
        ]


async def test_stream_ai_reply_to_foreign_message_degrades_to_null(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """A reply_to_id from another conversation is dropped, like a missing one."""
    headers, user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]
    other_id = await seed_conversation(session_factory, user_id=user["id"])
    foreign_id = await seed_message(
        session_factory, conversation_id=other_id, role="assistant", content="secret"
    )
    raw = json.dumps({"content": "Hi!", "reply_to_id": foreign_id})
    scripted_provider.events = [
        DeltaEvent(content=raw),
        CompletionEvent(content=raw, usage=Usage(input_tokens=3, output_tokens=1)),
    ]

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "hi"},
        headers=headers,
    )
    assert response.status_code == 200
    events = parse_sse((await response.aread()).decode())

    completed = events[-1][1]
    assert completed["message"]["content"] == "Hi!"
    assert completed["message"]["reply_to_id"] is None

    async with session_factory() as db:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        ).all()
        assert messages[-1].reply_to_id is None


async def test_stream_non_json_reply_falls_back_to_raw_text(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """A model that ignores the envelope still yields a usable, persisted reply."""
    raw = "You're welcome! I'm always here."
    scripted_provider.events = [
        DeltaEvent(content="You're welcome! "),
        DeltaEvent(content="I'm always here."),
        CompletionEvent(content=raw, usage=Usage(input_tokens=10, output_tokens=4)),
    ]
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "hi"},
        headers=headers,
    )
    assert response.status_code == 200
    events = parse_sse((await response.aread()).decode())

    # Non-JSON output is held until completion, then delivered in one piece.
    deltas = [data["content"] for name, data in events if name == "message.delta"]
    assert deltas == [raw]
    completed = events[-1][1]
    assert completed["message"]["content"] == raw
    assert completed["message"]["reply_to_id"] is None

    async with session_factory() as db:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        ).all()
        assert [(m.role, m.content) for m in messages] == [("user", "hi"), ("assistant", raw)]


async def test_stream_emoji_split_across_chunks_streams_intact(
    client: AsyncClient, session_factory: Any, scripted_provider: ScriptedProvider
) -> None:
    """A surrogate pair (escaped emoji) split by a chunk boundary must not crash
    SSE serialization: the high half is held back until its low half arrives,
    then the emoji streams whole."""
    raw = json.dumps({"content": "I love you 💕", "reply_to_id": None})  # ASCII 😍 escapes
    cut = raw.index("\\ud83d")  # split right between the pair's two escapes
    scripted_provider.events = [
        DeltaEvent(content=raw[:cut]),
        DeltaEvent(content=raw[cut : cut + 6]),
        DeltaEvent(content=raw[cut + 6 :]),
        CompletionEvent(content=raw, usage=Usage(input_tokens=5, output_tokens=3)),
    ]
    headers, _user = await auth_user(client, "alice")
    conversation = await create_conversation(client, headers)
    conversation_id = conversation["id"]

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "hi"},
        headers=headers,
    )
    assert response.status_code == 200
    body = (await response.aread()).decode()
    events = parse_sse(body)
    assert "error" not in [name for name, _data in events]

    deltas = [data["content"] for name, data in events if name == "message.delta"]
    assert "".join(deltas) == "I love you 💕"
    completed = events[-1][1]
    assert completed["message"]["content"] == "I love you 💕"

    async with session_factory() as db:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        ).all()
        assert [(m.role, m.content) for m in messages] == [
            ("user", "hi"),
            ("assistant", "I love you 💕"),
        ]
