"""AI model catalog: read access for everyone, CRUD for administrators.

Normal users see only active models (what they can start a conversation with);
administrators see and manage every row. Deletion is a deactivation (``ACTIVE=0``),
so conversations and telemetry that reference the model keep their foreign keys.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.db.models.ai import AIModel
from app.db.models.user import User
from app.db.repositories.ai import ModelRepository
from app.exceptions import BadRequestError, ConflictError, NotFoundError

EDITABLE_FIELDS = frozenset(
    {"provider_id", "endpoint_id", "model_name", "display_name", "context_window", "active"}
)


class ModelService:
    async def list_for_user(self, db: AsyncSession, actor: User) -> list[AIModel]:
        repo = ModelRepository(db)
        if actor.is_admin:
            return await repo.list_all()
        return await repo.list_active()

    async def get_for_user(self, db: AsyncSession, model_id: int, actor: User) -> AIModel:
        model = await ModelRepository(db).get_detailed(model_id, active_only=not actor.is_admin)
        if model is None:
            raise NotFoundError("Model not found")
        return model

    async def create(
        self,
        db: AsyncSession,
        *,
        provider_id: int,
        endpoint_id: int,
        model_name: str,
        display_name: str | None,
        context_window: int | None,
        active: bool,
    ) -> AIModel:
        """Create a model. Callers must already be administrators."""
        repo = ModelRepository(db)
        await self._validate_references(repo, provider_id=provider_id, endpoint_id=endpoint_id)
        await self._check_unique(
            repo, provider_id=provider_id, model_name=model_name, endpoint_id=endpoint_id
        )
        model = AIModel(
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            model_name=model_name,
            display_name=display_name,
            context_window=context_window,
            active=1 if active else 0,
            created_at=utcnow(),
        )
        await repo.add(model)
        await db.commit()
        await db.refresh(model, attribute_names=["provider"])
        return model

    async def update(self, db: AsyncSession, model_id: int, *, changes: dict[str, Any]) -> AIModel:
        repo = ModelRepository(db)
        model = await repo.get_detailed(model_id, active_only=False)
        if model is None:
            raise NotFoundError("Model not found")
        provider_id = changes.get("provider_id", model.provider_id)
        endpoint_id = changes.get("endpoint_id", model.endpoint_id)
        model_name = changes.get("model_name", model.model_name)
        await self._validate_references(repo, provider_id=provider_id, endpoint_id=endpoint_id)
        await self._check_unique(
            repo,
            provider_id=provider_id,
            model_name=model_name,
            endpoint_id=endpoint_id,
            exclude_id=model.id,
        )
        for field, value in changes.items():
            if field not in EDITABLE_FIELDS:  # ID/CREATED_AT are immutable
                continue
            setattr(model, field, (1 if value else 0) if field == "active" else value)
        await db.commit()
        await db.refresh(model, attribute_names=["provider"])
        return model

    async def deactivate(self, db: AsyncSession, model_id: int) -> None:
        """Soft delete: ACTIVE=0 keeps rows that reference the model valid."""
        model = await ModelRepository(db).get_detailed(model_id, active_only=False)
        if model is None:
            raise NotFoundError("Model not found")
        model.active = 0
        await db.commit()

    async def _validate_references(
        self, repo: ModelRepository, *, provider_id: int, endpoint_id: int
    ) -> None:
        if await repo.get_provider(provider_id) is None:
            raise BadRequestError("Unknown provider")
        if await repo.get_endpoint(endpoint_id) is None:
            raise BadRequestError("Unknown endpoint")

    async def _check_unique(
        self,
        repo: ModelRepository,
        *,
        provider_id: int,
        model_name: str,
        endpoint_id: int,
        exclude_id: int | None = None,
    ) -> None:
        """Respect UNIQUE (PROVIDER_ID, MODEL_NAME, ENDPOINT_ID)."""
        duplicate = await repo.find_duplicate(
            provider_id=provider_id,
            model_name=model_name,
            endpoint_id=endpoint_id,
            exclude_id=exclude_id,
        )
        if duplicate is not None:
            raise ConflictError("A model with this provider, name and endpoint already exists")
