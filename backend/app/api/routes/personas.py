"""User-persona endpoints — private to the authenticated user.

A persona describes who the *user* is role-playing as; it can be attached to a
conversation via `POST /conversations`. Every operation is scoped to the token's user.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.user import User
from app.schemas.personas import PersonaCreate, PersonaRead, PersonaUpdate
from app.services.persona_service import PersonaService

router = APIRouter(prefix="/personas", tags=["personas"])

persona_service = PersonaService()


@router.get("", response_model=list[PersonaRead], summary="List the user's personas")
async def list_personas(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[PersonaRead]:
    personas = await persona_service.list_for_user(db, user.id)
    return [PersonaRead.model_validate(persona) for persona in personas]


@router.post(
    "",
    response_model=PersonaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a persona",
    description=(
        "Only `name` is required. The persona belongs to the authenticated user; "
        "`user_id` is taken from the access token and never from the request body."
    ),
)
async def create_persona(
    body: PersonaCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PersonaRead:
    persona = await persona_service.create(
        db,
        user_id=user.id,
        name=body.name,
        gender=body.gender,
        description=body.description,
        age=body.age,
    )
    return PersonaRead.model_validate(persona)


@router.get("/{persona_id}", response_model=PersonaRead, summary="Get one of the user's personas")
async def get_persona(
    persona_id: Annotated[int, Path(ge=1)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PersonaRead:
    persona = await persona_service.get_for_user(db, persona_id, user.id)
    return PersonaRead.model_validate(persona)


@router.patch("/{persona_id}", response_model=PersonaRead, summary="Update a persona")
async def update_persona(
    persona_id: Annotated[int, Path(ge=1)],
    body: PersonaUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PersonaRead:
    persona = await persona_service.update(db, persona_id, user.id, changes=body.changes())
    return PersonaRead.model_validate(persona)


@router.delete(
    "/{persona_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a persona",
    description=(
        "Conversations that used the persona are kept and fall back to no persona "
        "(USER_PERSONA_ID becomes NULL)."
    ),
)
async def delete_persona(
    persona_id: Annotated[int, Path(ge=1)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await persona_service.delete(db, persona_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
