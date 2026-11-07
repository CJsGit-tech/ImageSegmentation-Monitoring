import asyncio

from alembic import context
from sqlalchemy import Connection

import app.models
from app.core.config import get_settings
from app.db.session import create_unpooled_engine

config = context.config
target_metadata = app.models.Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_with_own_engine() -> None:
    engine = create_unpooled_engine(get_settings())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        # Command-line use: alembic upgrade head, alembic revision --autogenerate.
        asyncio.run(_run_with_own_engine())
    else:
        # Application startup: app.db.migrate already opened the connection.
        _run(connection)


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported.")
run_online()
