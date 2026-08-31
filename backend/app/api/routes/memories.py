"""Memory endpoints — the user's own long-term memories (per character)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.user import User
from app.db.repositories.memories import MemoryRepository
from app.schemas.memories import MemoryRead

router = APIRouter(tags=["memories"])


@router.get(
    "/memories",
    response_model=list[MemoryRead],
    summary="List the user's memories",
    description=(
        "Long-term memories scoped to the authenticated user, optionally filtered "
        "by character. Only active memories are returned."
    ),
)
async def list_memories(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    character_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[MemoryRead]:
    memories = await MemoryRepository(db).list_for_user(
        user.id, character_id=character_id, limit=limit
    )
    return [MemoryRead.model_validate(memory) for memory in memories]
