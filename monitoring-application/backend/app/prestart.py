"""Container entrypoint: check settings, wait for the database, migrate, then serve.

Migrations run here rather than in the FastAPI lifespan so that they run once per
container start, before the port opens, and outside uvicorn's event loop.
"""

import asyncio
import logging
import sys
import time

import uvicorn
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.bootstrap import ensure_initial_admin
from app.core.config import ConfigurationError, Settings, get_settings
from app.core.logging import configure_logging
from app.db.migrate import upgrade_to_head
from app.db.session import create_unpooled_engine, ping

logger = logging.getLogger("app.prestart")

RETRY_INTERVAL_SECONDS = 2


async def _wait_for_database(engine: AsyncEngine, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            await ping(engine)
            return
        except (OperationalError, InterfaceError) as exc:
            if time.monotonic() >= deadline:
                raise
            logger.info("waiting for database", extra={"reason": type(exc.orig).__name__})
            await asyncio.sleep(RETRY_INTERVAL_SECONDS)


async def _prepare_database(settings: Settings) -> None:
    engine = create_unpooled_engine(settings)
    try:
        await _wait_for_database(engine, settings.db_startup_timeout_seconds)
        await upgrade_to_head(engine)
        # Before the port opens, so that nobody can register this account name first.
        await ensure_initial_admin(engine, settings)
    finally:
        await engine.dispose()


def main() -> int:
    configure_logging()
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        logger.error("startup aborted: %s", exc)
        return 1
    configure_logging(settings.log_level)

    try:
        asyncio.run(_prepare_database(settings))
    except ConfigurationError as exc:
        logger.error("startup aborted: %s", exc)
        return 1
    except Exception:
        logger.exception("startup aborted: database preparation failed")
        return 1
    logger.info("database schema is up to date")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
