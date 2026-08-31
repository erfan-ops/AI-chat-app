"""Database engine and session management.

The engine is created lazily (no connection is opened at import time). Sessions are
short-lived: request handlers use the ``get_db`` dependency, and long-running flows
(AI streaming) open their own sessions so no connection is held across a stream.

``fetch_lobs=True`` makes the oracledb driver fetch CLOB columns as plain strings in
asyncio mode instead of returning LOB objects.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import oracledb
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

# In oracledb's asyncio mode CLOBs are fetched as LOB objects unless this global
# is set; with it they arrive as plain strings (the async connect() path does
# not accept fetch_lobs as a per-connection argument).
oracledb.defaults.fetch_lobs = True


class Base(DeclarativeBase):
    """Declarative base for all ORM models (maps the existing Oracle schema)."""


engine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a request-scoped session, always closed."""
    async with SessionFactory() as session:
        yield session
