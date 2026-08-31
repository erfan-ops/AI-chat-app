"""Memory schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    character_id: int
    content: str
    memory_type: str
    importance: float | None
    confidence: float | None
    created_at: datetime
    last_accessed_at: datetime | None
