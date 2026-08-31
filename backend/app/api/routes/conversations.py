"""Conversation endpoints. Every operation is scoped to the authenticated user."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.conversation import Conversation
from app.db.models.user import User
from app.schemas.conversations import ConversationCreate, ConversationRead, ConversationUpdate
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])

conversation_service = ConversationService()


@router.get("", response_model=list[ConversationRead], summary="List the user's conversations")
async def list_conversations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Conversation]:
    return await conversation_service.list_for_user(db, user.id, limit=limit, offset=offset)


@router.post(
    "",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new conversation",
    description=(
        "Starts a conversation with an AI character. The model defaults to the "
        "user's default model, then to the first active model."
    ),
)
async def create_conversation(
    body: ConversationCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Conversation:
    return await conversation_service.create(
        db,
        user_id=user.id,
        character_id=body.character_id,
        model_id=body.model_id,
        title=body.title,
        user_persona_id=body.user_persona_id,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationRead,
    summary="Get one of the user's conversations",
)
async def get_conversation(
    conversation_id: Annotated[int, Path(ge=1)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Conversation:
    return await conversation_service.get_for_user(db, conversation_id, user.id)


@router.patch(
    "/{conversation_id}",
    response_model=ConversationRead,
    summary="Rename a conversation",
)
async def update_conversation(
    conversation_id: Annotated[int, Path(ge=1)],
    body: ConversationUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Conversation:
    return await conversation_service.update_title(db, conversation_id, user.id, title=body.title)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation (soft delete)",
    description="Marks the conversation DELETED; the row and its messages are preserved.",
)
async def delete_conversation(
    conversation_id: Annotated[int, Path(ge=1)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await conversation_service.soft_delete(db, conversation_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
