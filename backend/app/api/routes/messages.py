"""Message endpoints: history retrieval and the streamed AI chat endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import (
    get_ai_service,
    get_current_user,
    get_provider_factory,
    get_session_factory,
)
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.db.models.message import Message
from app.db.models.user import User
from app.schemas.messages import MessageCreate, MessageRead
from app.services.ai_service import AIService, ProviderFactory
from app.services.message_service import MessageService

router = APIRouter(prefix="/conversations", tags=["messages"])

message_service = MessageService()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageRead],
    summary="List a conversation's messages",
    description=(
        "Returns messages in chronological order. Use `before_id` (exclusive) with "
        "`limit` to page backwards through history."
    ),
)
async def list_messages(
    conversation_id: Annotated[int, Path(ge=1)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[Message]:
    return await message_service.list_messages(
        db,
        conversation_id=conversation_id,
        user_id=user.id,
        limit=limit,
        before_id=before_id,
    )


@router.post(
    "/{conversation_id}/messages",
    response_class=StreamingResponse,
    summary="Send a message and stream the AI reply (Server-Sent Events)",
    description=(
        "Persists the user message, then streams the AI reply incrementally as SSE. "
        "Events: `message.created` (the persisted user message), repeated "
        "`message.delta` (incremental text), `message.completed` (the persisted "
        "assistant message + token usage), or `error` if the AI provider fails."
    ),
    responses={
        200: {
            "description": "SSE stream",
            "content": {"text/event-stream": {}},
        },
        401: {"description": "Missing or invalid access token"},
        404: {"description": "Conversation not found or not owned by the caller"},
    },
)
async def send_message(
    conversation_id: Annotated[int, Path(ge=1)],
    body: MessageCreate,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    provider_factory: Annotated[ProviderFactory, Depends(get_provider_factory)],
) -> StreamingResponse:
    ai_service: AIService = get_ai_service(settings)
    # Ownership check + persistence happen before the response starts, so HTTP
    # errors (404 etc.) are returned as proper status codes, not SSE.
    prepared = await ai_service.prepare_message(
        conversation_id=conversation_id,
        user_id=user.id,
        content=body.content,
        reply_to_id=body.reply_to_id,
        client_timezone=body.client_timezone,
        client_utc_offset_minutes=body.client_utc_offset_minutes,
        session_factory=session_factory,
    )
    return StreamingResponse(
        ai_service.stream_response(
            prepared,
            session_factory=session_factory,
            provider_factory=provider_factory,
            request=request,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
