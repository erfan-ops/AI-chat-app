"""AI model catalog endpoints (API keys and base URLs are never exposed).

``GET`` is available to any authenticated user (active models only; administrators
also see inactive ones). Creating, updating and deleting models is administrator-only
via the ``require_admin`` dependency.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_admin
from app.db.database import get_db
from app.db.models.ai import AIModel
from app.db.models.user import User
from app.schemas.ai import ModelCreate, ModelRead, ModelUpdate
from app.services.model_service import ModelService

router = APIRouter(tags=["ai"])

model_service = ModelService()


def _to_read(model: AIModel) -> ModelRead:
    """Serialize a model row; the provider relationship contributes its name only."""
    return ModelRead(
        id=model.id,
        model_name=model.model_name,
        display_name=model.display_name,
        provider=model.provider.name_ if model.provider else None,
        provider_id=model.provider_id,
        context_window=model.context_window,
        active=model.is_active,
        endpoint_id=model.endpoint_id,
        created_at=model.created_at,
    )


@router.get(
    "/models",
    response_model=list[ModelRead],
    summary="List AI models",
    description=(
        "Models available for conversations; used to pick a conversation's model. "
        "Administrators also see inactive models."
    ),
)
async def list_models(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ModelRead]:
    models = await model_service.list_for_user(db, user)
    return [_to_read(model) for model in models]


@router.post(
    "/models",
    response_model=ModelRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an AI model (admin)",
    description="Administrators only. The provider and endpoint must already exist.",
)
async def create_model(
    body: ModelCreate,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ModelRead:
    model = await model_service.create(
        db,
        provider_id=body.provider_id,
        endpoint_id=body.endpoint_id,
        model_name=body.model_name,
        display_name=body.display_name,
        context_window=body.context_window,
        active=body.active,
    )
    return _to_read(model)


@router.get(
    "/models/{model_id}",
    response_model=ModelRead,
    summary="Get one AI model",
    description="Active models for any user; administrators can also fetch inactive ones.",
)
async def get_model(
    model_id: Annotated[int, Path(ge=1)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ModelRead:
    model = await model_service.get_for_user(db, model_id, user)
    return _to_read(model)


@router.patch(
    "/models/{model_id}",
    response_model=ModelRead,
    summary="Update an AI model (admin)",
    description="Administrators only.",
)
async def update_model(
    model_id: Annotated[int, Path(ge=1)],
    body: ModelUpdate,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ModelRead:
    model = await model_service.update(db, model_id, changes=body.changes())
    return _to_read(model)


@router.delete(
    "/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an AI model (admin, soft delete)",
    description=(
        "Administrators only. Sets ACTIVE=0 so conversations and telemetry that "
        "reference the model keep their foreign keys."
    ),
)
async def delete_model(
    model_id: Annotated[int, Path(ge=1)],
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await model_service.deactivate(db, model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
