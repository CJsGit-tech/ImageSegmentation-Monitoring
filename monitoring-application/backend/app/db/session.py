"""Async engine and session helper. All database access goes through SQLAlchemy."""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select, literal
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings


def create_pooled_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        pool_pre_ping=True,
    )


def create_unpooled_engine(settings: Settings) -> AsyncEngine:
    """For short jobs that run on their own event loop, such as migrations."""
    return create_async_engine(settings.database_url, poolclass=NullPool)


@lru_cache
def get_engine() -> AsyncEngine:
    return create_pooled_engine(get_settings())


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Handlers commit explicitly. Anything uncommitted is rolled back on close."""
    async with get_session_factory()() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


async def ping(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(select(literal(1)))
