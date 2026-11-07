"""Alembic helpers. Alembic runs through the async engine with run_sync."""

from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Column, Connection, MetaData, String, Table, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Alembic's own bookkeeping table, described here only to read it. Not part of Base.metadata.
_alembic_version = Table("alembic_version", MetaData(), Column("version_num", String(32)))


def alembic_config() -> Config:
    return Config(_BACKEND_ROOT / "alembic.ini")


def _upgrade(connection: Connection, config: Config) -> None:
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def upgrade_to_head(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade, alembic_config())


@lru_cache
def expected_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


async def current_revision(connection: AsyncConnection) -> str | None:
    """Returns None when migrations have not run yet."""
    try:
        result = await connection.execute(select(_alembic_version.c.version_num))
    except ProgrammingError:
        return None
    return result.scalar_one_or_none()
