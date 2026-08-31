"""Shared fixtures: an in-memory SQLite database (same ORM models as Oracle),
dependency overrides, a scripted AI provider, and API helpers.

The tests run entirely against SQLite — the Oracle-specific columns are mapped
with dialect-neutral types, and live Oracle verification happens separately.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.ai.base import (
    ChatRequest,
    CompletionEvent,
    DeltaEvent,
    StreamEvent,
    Usage,
)
from app.api.dependencies import (
    get_auth_service,
    get_provider_factory,
    get_session_factory,
    get_settings,
)
from app.core.config import Settings
from app.core.time import utcnow
from app.db.database import Base, get_db
from app.db.models.ai import AIEndpoint, AIModel, Provider
from app.db.models.character import Character
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.user import ADMIN_ROLE, User
from app.main import app

TEST_SETTINGS = Settings(
    jwt_secret="test-secret-key-for-testing-only-0123456789",
    database_url="sqlite+aiosqlite://",
)

TEST_PASSWORD = "password123"

DEFAULT_CHUNKS = ["Hello", ", I", " was thinking", " about you."]
DEFAULT_REPLY = "".join(DEFAULT_CHUNKS)


class ScriptedProvider:
    """AI provider whose events and timing are fully controlled by the test."""

    def __init__(self, events: list[StreamEvent], *, delay: float = 0.0) -> None:
        self.events = events
        self.delay = delay
        self.closed = False
        self.yielded: list[StreamEvent] = []
        self.requests: list[ChatRequest] = []

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        try:
            for event in self.events:
                if self.delay:
                    await asyncio.sleep(self.delay)
                self.yielded.append(event)
                yield event
        finally:
            self.closed = True


@pytest_asyncio.fixture
async def scripted_provider() -> ScriptedProvider:
    """Default happy-path provider: four deltas + a completion with usage."""
    return ScriptedProvider(
        [DeltaEvent(content=chunk) for chunk in DEFAULT_CHUNKS]
        + [CompletionEvent(content=DEFAULT_REPLY, usage=Usage(input_tokens=12, output_tokens=5))]
    )


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[Any]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def catalog(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[None]:
    """Seed the AI catalog (provider, endpoint, model, character) into the test DB."""
    now = utcnow()
    async with session_factory() as db:
        db.add(Provider(name_="DeepSeek"))
        await db.flush()
        db.add(AIEndpoint(name="DeepSeek", base_url="https://api.deepseek.com", api_key="test-key"))
        await db.flush()
        db.add(
            AIModel(
                provider_id=1,
                model_name="deepseek-v4-pro",
                display_name="DeepSeek v4 Pro",
                context_window=1000,
                active=1,
                endpoint_id=1,
                created_at=now,
            )
        )
        db.add(
            Character(
                name="Maya",
                description="A warm and caring companion.",
                # SYSTEM_PROMPT is the authoritative persona prompt (see docs/database.md).
                system_prompt="You are Maya, a warm and caring companion.",
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _reset_login_attempts() -> AsyncIterator[None]:
    """Keep the login throttle from leaking state between tests."""
    yield
    get_auth_service(TEST_SETTINGS).reset_attempts()


@pytest_asyncio.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    scripted_provider: ScriptedProvider,
) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    def override_provider_factory() -> Any:
        # Zero-arg callable (FastAPI would treat any parameter name as a dependency)
        # that resolves to the factory the route expects.
        return lambda _endpoint, _timeout: scripted_provider

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_provider_factory] = override_provider_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()


# -- Helpers ------------------------------------------------------------------------


async def register_user(
    client: AsyncClient,
    username: str = "alice",
    password: str = TEST_PASSWORD,
    display_name: str | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/auth/register",
        json={"username": username, "password": password, "display_name": display_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def login_user(
    client: AsyncClient, username: str, password: str = TEST_PASSWORD
) -> dict[str, Any]:
    response = await client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


async def auth_user(
    client: AsyncClient, username: str = "alice", password: str = TEST_PASSWORD
) -> tuple[dict[str, str], dict[str, Any]]:
    """Register + log in; returns (auth headers, user dict)."""
    await register_user(client, username, password)
    login = await login_user(client, username, password)
    return {"Authorization": f"Bearer {login['access_token']}"}, login["user"]


async def create_conversation(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    character_id: int = 1,
    model_id: int | None = None,
    title: str | None = None,
    user_persona_id: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"character_id": character_id}
    if model_id is not None:
        body["model_id"] = model_id
    if title is not None:
        body["title"] = title
    if user_persona_id is not None:
        body["user_persona_id"] = user_persona_id
    response = await client.post("/conversations", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def create_persona(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    name: str = "Alex",
    **optional: Any,
) -> dict[str, Any]:
    """POST /personas. ``optional`` (gender, description, age) is sent verbatim."""
    body: dict[str, Any] = {"name": name, **optional}
    response = await client.post("/personas", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def promote_to_admin(session_factory: async_sessionmaker[AsyncSession], user_id: int) -> None:
    """Grant ROLE_admin in the database — no endpoint can do this."""
    async with session_factory() as db:
        user = (await db.scalars(select(User).where(User.id == user_id))).first()
        assert user is not None
        user.role = ADMIN_ROLE
        await db.commit()


async def auth_admin(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    username: str = "root",
    password: str = TEST_PASSWORD,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Register a user, promote them in the DB, then log in; returns (headers, user)."""
    user = await register_user(client, username, password)
    await promote_to_admin(session_factory, user["id"])
    login = await login_user(client, username, password)
    assert login["user"]["role"] == ADMIN_ROLE
    return {"Authorization": f"Bearer {login['access_token']}"}, login["user"]


async def create_character(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    name: str = "Nova",
    description: str | None = None,
    avatar_url: str | None = None,
    system_prompt: str | None = None,
    **admin_fields: Any,
) -> dict[str, Any]:
    """POST /characters. ``admin_fields`` (owner_user_id, status) are sent verbatim,
    including an explicit ``None``, so admin-only behavior can be exercised."""
    body: dict[str, Any] = {"name": name}
    for key, value in (
        ("description", description),
        ("avatar_url", avatar_url),
        ("system_prompt", system_prompt),
    ):
        if value is not None:
            body[key] = value
    body.update(admin_fields)
    response = await client.post("/characters", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def seed_conversation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    character_id: int = 1,
    model_id: int = 1,
    status: str = "ACTIVE",
) -> int:
    async with session_factory() as db:
        conversation = Conversation(
            user_id=user_id,
            character_id=character_id,
            model_id=model_id,
            title="Seed conversation",
            status=status,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
        return conversation.id


async def seed_message(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: int,
    role: str,
    content: str,
) -> int:
    async with session_factory() as db:
        message = Message(
            conversation_id=conversation_id, role=role, content=content, created_at=utcnow()
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message.id


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into [(event_name, data_dict), ...]."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name: str | None = None
        data: dict[str, Any] | None = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
        if event_name is not None and data is not None:
            events.append((event_name, data))
    return events


def make_request() -> Request:
    """A stub starlette Request whose client is always connected."""

    async def receive() -> dict[str, str]:
        return {"type": "http.request"}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/test",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
            "http_version": "1.1",
        },
        receive,
    )
