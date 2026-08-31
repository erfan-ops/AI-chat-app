"""AI service: resolves provider config from the DB, builds context, streams + persists.

The streaming flow is deliberately session-lean: every database touch happens in
its own short session (pre-flight, then final persistence), so no connection is
held open while the AI provider streams.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import ClientDisconnect, Request

from app.ai.base import (
    AIProvider,
    ChatMessage,
    ChatRequest,
    CompletionEvent,
    DeltaEvent,
    EndpointConfig,
    ErrorEvent,
)
from app.ai.context import ConversationContext, build_conversation_context
from app.core.config import Settings
from app.core.logging import get_logger, structured
from app.core.time import utcnow
from app.db.models.ai import AIModel
from app.db.models.conversation import Conversation
from app.db.models.message import Message, MessageGeneration
from app.db.repositories.ai import ModelRepository
from app.db.repositories.characters import CharacterRepository
from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.memories import MemoryRepository
from app.db.repositories.messages import GenerationRepository, MessageRepository
from app.db.repositories.personas import PersonaRepository
from app.exceptions import NotFoundError, ServiceUnavailableError
from app.schemas.messages import (
    MessageCompletedPayload,
    MessageCreatedPayload,
    MessageDeltaPayload,
    MessageRead,
    StreamErrorPayload,
    UsageData,
)

logger = get_logger("app.services.ai")

ProviderFactory = Callable[[EndpointConfig, httpx.Timeout], AIProvider]
SessionFactory = async_sessionmaker[AsyncSession]

# Rows fetched for history; the context builder trims further by budget.
MAX_HISTORY_FETCH = 500

GENERATION_PURPOSE_CHAT = "chat"


def sse_encode(event: str, payload: BaseModel) -> str:
    """Format one Server-Sent Event from a pydantic payload."""
    return f"event: {event}\ndata: {payload.model_dump_json()}\n\n"


@dataclass(frozen=True)
class PreparedStream:
    """Everything resolved before the stream starts (no DB session held open)."""

    conversation_id: int
    user_message: Message
    context: ConversationContext
    endpoint: EndpointConfig
    model_id: int
    temperature: float
    max_tokens: int


class AIService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # -- Pre-flight: runs before the StreamingResponse starts (HTTP errors possible) --

    async def prepare_message(
        self,
        *,
        conversation_id: int,
        user_id: int,
        content: str,
        reply_to_id: int | None = None,
        session_factory: SessionFactory,
    ) -> PreparedStream:
        """Verify ownership, persist the user message, and build the model context."""
        settings = self._settings

        async with session_factory() as db:
            conversation = await ConversationRepository(db).get_active_for_user(
                conversation_id, user_id
            )
            if conversation is None:
                raise NotFoundError("Conversation not found")
            if reply_to_id is not None:
                # The reply target must exist *and* belong to this conversation —
                # a foreign message id must never leak via the quote metadata.
                target = await MessageRepository(db).get_for_conversation(
                    reply_to_id, conversation_id
                )
                if target is None:
                    raise NotFoundError("Reply target not found")
            now = utcnow()
            user_message = await MessageRepository(db).add(
                Message(
                    conversation_id=conversation_id,
                    role="user",
                    content=content,
                    reply_to_id=reply_to_id,
                    created_at=now,
                )
            )
            conversation.last_message_at = now
            conversation.updated_at = now
            await db.commit()
            await db.refresh(user_message)
            character_id, model_id = conversation.character_id, conversation.model_id
            persona_id = conversation.user_persona_id

        async with session_factory() as db:
            character = await CharacterRepository(db).get_by_id(character_id)
            model = await ModelRepository(db).get_with_config(model_id)
            if model is None or model.endpoint is None:
                raise ServiceUnavailableError("The conversation's AI model is not configured")
            history = await MessageRepository(db).list_for_conversation(
                conversation_id, limit=MAX_HISTORY_FETCH
            )
            memories = await MemoryRepository(db).top_for_context(
                user_id, character_id, limit=settings.ai_max_memories
            )
            # Only queried when the conversation actually has a persona.
            persona = (
                await PersonaRepository(db).get_for_user(persona_id, user_id)
                if persona_id is not None
                else None
            )

        endpoint = self._resolve_endpoint(model, settings)
        context = build_conversation_context(
            character_system_prompt=character.system_prompt if character else None,
            memories=memories,
            messages=history,
            context_window=model.context_window,
            max_messages=settings.ai_context_max_messages,
            default_context_chars=settings.ai_default_context_chars,
            max_memories=settings.ai_max_memories,
            user_persona=persona,
        )
        structured(
            logger,
            logging.INFO,
            "prepared AI message",
            conversation_id=conversation_id,
            message_id=user_message.id,
            context_messages=len(context.messages),
            model=model.model_name,
        )
        return PreparedStream(
            conversation_id=conversation_id,
            user_message=user_message,
            context=context,
            endpoint=endpoint,
            model_id=model_id,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
        )

    # -- Streaming: no DB access until the response is complete --------------------

    async def stream_response(
        self,
        prepared: PreparedStream,
        *,
        session_factory: SessionFactory,
        provider_factory: ProviderFactory,
        request: Request,
    ) -> AsyncIterator[str]:
        """Stream the AI reply as SSE; persist it (plus telemetry) on completion.

        Events: ``message.created`` → ``message.delta``* → ``message.completed``,
        or ``error`` if the provider fails. If the client disconnects, the
        provider stream is cancelled and nothing partial is persisted.
        """
        settings = self._settings
        yield sse_encode(
            "message.created",
            MessageCreatedPayload(
                conversation_id=prepared.conversation_id,
                message=MessageRead.model_validate(prepared.user_message),
            ),
        )

        chat_request = ChatRequest(
            model=prepared.endpoint.model,
            messages=[
                ChatMessage(role="system", content=prepared.context.system_prompt),
                *prepared.context.messages,
            ],
            temperature=prepared.temperature,
            max_tokens=prepared.max_tokens,
        )
        timeout = httpx.Timeout(
            connect=10.0, read=settings.ai_stream_timeout_seconds, write=30.0, pool=10.0
        )
        provider = provider_factory(prepared.endpoint, timeout)
        started = time.perf_counter()
        completion: CompletionEvent | None = None
        try:
            async with aclosing(provider.stream_chat(chat_request)) as stream:
                async for event in stream:
                    if await request.is_disconnected():
                        structured(
                            logger,
                            logging.INFO,
                            "client disconnected during AI stream",
                            conversation_id=prepared.conversation_id,
                        )
                        return
                    if isinstance(event, DeltaEvent):
                        yield sse_encode(
                            "message.delta", MessageDeltaPayload(content=event.content)
                        )
                    elif isinstance(event, CompletionEvent):
                        completion = event
                    elif isinstance(event, ErrorEvent):
                        structured(
                            logger,
                            logging.WARNING,
                            "AI provider error",
                            conversation_id=prepared.conversation_id,
                            detail=event.message,
                        )
                        yield sse_encode(
                            "error",
                            StreamErrorPayload(
                                code="provider_error",
                                detail="The AI service failed to respond. Please try again.",
                            ),
                        )
                        return
        except GeneratorExit:
            # The client (or server) closed the stream; aclosing() closed the provider.
            structured(
                logger,
                logging.INFO,
                "AI stream cancelled",
                conversation_id=prepared.conversation_id,
            )
            return
        except asyncio.CancelledError:
            structured(
                logger,
                logging.INFO,
                "AI stream task cancelled",
                conversation_id=prepared.conversation_id,
            )
            raise
        except ClientDisconnect:
            structured(
                logger,
                logging.INFO,
                "client disconnected during AI stream",
                conversation_id=prepared.conversation_id,
            )
            return
        except Exception:
            logger.exception(
                "unexpected error while streaming AI response conversation_id=%s",
                prepared.conversation_id,
            )
            yield sse_encode(
                "error",
                StreamErrorPayload(
                    code="provider_error",
                    detail="The AI service failed to respond. Please try again.",
                ),
            )
            return

        if completion is None:
            structured(
                logger,
                logging.WARNING,
                "AI stream ended without completion",
                conversation_id=prepared.conversation_id,
            )
            yield sse_encode(
                "error",
                StreamErrorPayload(
                    code="stream_interrupted",
                    detail="The AI stream ended before the response completed.",
                ),
            )
            return

        latency_ms = int((time.perf_counter() - started) * 1000)
        assistant_message = await self._persist_assistant_message(
            prepared, completion, latency_ms=latency_ms, session_factory=session_factory
        )
        yield sse_encode(
            "message.completed",
            MessageCompletedPayload(
                message=MessageRead.model_validate(assistant_message),
                usage=(
                    UsageData(
                        input_tokens=completion.usage.input_tokens,
                        output_tokens=completion.usage.output_tokens,
                        total_tokens=completion.usage.total_tokens,
                    )
                    if completion.usage
                    else None
                ),
                latency_ms=latency_ms,
            ),
        )

    # -- Internal helpers ----------------------------------------------------------

    def _resolve_endpoint(self, model: AIModel, settings: Settings) -> EndpointConfig:
        if settings.ai_provider != "database":
            return EndpointConfig(
                provider_name=settings.ai_provider,
                base_url=settings.ai_base_url,
                api_key=settings.ai_api_key,
                model=settings.ai_model or model.model_name,
            )
        endpoint = model.endpoint
        if endpoint is None:
            raise ServiceUnavailableError("The conversation's AI model is not configured")
        provider_name = model.provider.name_ if model.provider else "openai"
        return EndpointConfig(
            provider_name=provider_name,
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
            model=model.model_name,
        )

    async def _persist_assistant_message(
        self,
        prepared: PreparedStream,
        completion: CompletionEvent,
        *,
        latency_ms: int,
        session_factory: SessionFactory,
    ) -> Message:
        """Persist the completed AI reply + generation telemetry in one transaction."""
        now = utcnow()
        usage = completion.usage
        async with session_factory() as db:
            message = await MessageRepository(db).add(
                Message(
                    conversation_id=prepared.conversation_id,
                    role="assistant",
                    content=completion.content,
                    created_at=now,
                )
            )
            await GenerationRepository(db).add(
                MessageGeneration(
                    message_id=message.id,
                    model_id=prepared.model_id,
                    purpose=GENERATION_PURPOSE_CHAT,
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    total_tokens=usage.total_tokens if usage else None,
                    latency_ms=latency_ms,
                    temperature=prepared.temperature,
                    created_at=now,
                )
            )
            conversation = await db.get(Conversation, prepared.conversation_id)
            if conversation is not None:
                conversation.last_message_at = now
                conversation.updated_at = now
            await db.commit()
            await db.refresh(message)
        structured(
            logger,
            logging.INFO,
            "persisted AI response",
            conversation_id=prepared.conversation_id,
            message_id=message.id,
            latency_ms=latency_ms,
        )
        return message
