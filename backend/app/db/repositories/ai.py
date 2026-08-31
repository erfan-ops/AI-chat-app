"""Data access for PROVIDERS / AI_ENDPOINTS / AI_MODELS."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.ai import AIEndpoint, AIModel, Provider


class ModelRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_active(self) -> list[AIModel]:
        stmt = (
            select(AIModel)
            .where(AIModel.active == 1)
            .options(selectinload(AIModel.provider))
            .order_by(AIModel.id)
        )
        return list((await self._db.scalars(stmt)).all())

    async def list_all(self) -> list[AIModel]:
        """Every model, active or not (administrators only)."""
        stmt = select(AIModel).options(selectinload(AIModel.provider)).order_by(AIModel.id)
        return list((await self._db.scalars(stmt)).all())

    async def get_active(self, model_id: int) -> AIModel | None:
        stmt = select(AIModel).where(AIModel.id == model_id, AIModel.active == 1)
        return (await self._db.scalars(stmt)).first()

    async def get_detailed(self, model_id: int, *, active_only: bool) -> AIModel | None:
        """Model with its provider loaded, for serialization."""
        stmt = select(AIModel).where(AIModel.id == model_id).options(selectinload(AIModel.provider))
        if active_only:
            stmt = stmt.where(AIModel.active == 1)
        return (await self._db.scalars(stmt)).first()

    async def first_active(self) -> AIModel | None:
        stmt = select(AIModel).where(AIModel.active == 1).order_by(AIModel.id).limit(1)
        return (await self._db.scalars(stmt)).first()

    async def get_with_config(self, model_id: int) -> AIModel | None:
        """Model with provider + endpoint eagerly loaded (for AI generation config)."""
        stmt = (
            select(AIModel)
            .where(AIModel.id == model_id)
            .options(selectinload(AIModel.provider), selectinload(AIModel.endpoint))
        )
        return (await self._db.scalars(stmt)).first()

    async def find_duplicate(
        self, *, provider_id: int, model_name: str, endpoint_id: int, exclude_id: int | None = None
    ) -> AIModel | None:
        """Existing row for the UNIQUE (PROVIDER_ID, MODEL_NAME, ENDPOINT_ID) triple."""
        stmt = select(AIModel).where(
            AIModel.provider_id == provider_id,
            AIModel.model_name == model_name,
            AIModel.endpoint_id == endpoint_id,
        )
        if exclude_id is not None:
            stmt = stmt.where(AIModel.id != exclude_id)
        return (await self._db.scalars(stmt)).first()

    async def get_provider(self, provider_id: int) -> Provider | None:
        return (await self._db.scalars(select(Provider).where(Provider.id == provider_id))).first()

    async def get_endpoint(self, endpoint_id: int) -> AIEndpoint | None:
        return (
            await self._db.scalars(select(AIEndpoint).where(AIEndpoint.id == endpoint_id))
        ).first()

    async def add(self, model: AIModel) -> AIModel:
        """Stage an insert; the caller commits."""
        self._db.add(model)
        await self._db.flush()
        return model
