"""AI character (persona) catalog endpoints.

Shared paths: a normal user is scoped to built-in characters plus their own, an
administrator (``ROLE_admin``) reaches every character. All of that is decided from
the authenticated user's stored role — never from the request body.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.ai import CharacterCreate, CharacterRead, CharacterUpdate
from app.services.character_service import CharacterService

router = APIRouter(tags=["characters"])

character_service = CharacterService()


@router.get(
    "/characters",
    response_model=list[CharacterRead],
    summary="List AI characters",
    description=(
        "Built-in characters plus the ones the authenticated user created. "
        "Another user's private characters are never listed. Administrators see "
        "every character, whatever its owner or status."
    ),
)
async def list_characters(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[CharacterRead]:
    characters = await character_service.list_for_user(db, user)
    return [CharacterRead.model_validate(character) for character in characters]


@router.post(
    "/characters",
    response_model=CharacterRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an AI character",
    description=(
        "Creates a character owned by the authenticated user; the owner comes from the "
        "access token, so the character is visible only to its creator. Administrators "
        "may also pass `owner_user_id` (explicit `null` creates a global character) and "
        "`status`; for anyone else those fields are rejected with 403."
    ),
)
async def create_character(
    body: CharacterCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CharacterRead:
    character = await character_service.create(
        db,
        actor=user,
        name=body.name,
        description=body.description,
        avatar_url=body.avatar_url,
        system_prompt=body.system_prompt,
        owner_user_id=body.owner_user_id,
        status=body.status,
        admin_fields=body.admin_fields,
    )
    return CharacterRead.model_validate(character)


@router.get(
    "/characters/{character_id}",
    response_model=CharacterRead,
    summary="Get one AI character",
)
async def get_character(
    character_id: Annotated[int, Path(ge=1)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CharacterRead:
    character = await character_service.get_for_user(db, character_id, user)
    return CharacterRead.model_validate(character)


@router.patch(
    "/characters/{character_id}",
    response_model=CharacterRead,
    summary="Update an AI character",
    description=(
        "Owners may edit their own characters; administrators may edit any character "
        "and additionally reassign `owner_user_id` or set `status`."
    ),
)
async def update_character(
    character_id: Annotated[int, Path(ge=1)],
    body: CharacterUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CharacterRead:
    character = await character_service.update(
        db,
        character_id,
        actor=user,
        changes=body.changes(),
        admin_fields=body.admin_fields,
    )
    return CharacterRead.model_validate(character)


@router.delete(
    "/characters/{character_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an AI character (soft delete)",
    description=(
        "Marks the character DELETED; the row and any conversations referencing it are "
        "preserved. Owners may delete their own characters, administrators any character."
    ),
)
async def delete_character(
    character_id: Annotated[int, Path(ge=1)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await character_service.soft_delete(db, character_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
