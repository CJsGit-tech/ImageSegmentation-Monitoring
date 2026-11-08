"""Automatic cleanup of system log rows older than LOG_RETENTION_DAYS (PRD-14-R10).

Runs inside the backend process, at startup and then periodically. Deleting is idempotent, so
several backend processes may run it at once.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.system_log import SystemLog

logger = logging.getLogger("app.syslog")

DELETE_CHUNK = 5000
MAX_CHECK_SECONDS = 3600.0
MIN_CHECK_SECONDS = 10.0


def cutoff_for(retention_days: float, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return now - timedelta(days=retention_days)


async def purge_expired(
    session_factory: async_sessionmaker[AsyncSession], retention_days: float, now: datetime | None = None
) -> int:
    """Deletes rows that happened before now minus the retention period. Returns how many."""
    cutoff = cutoff_for(retention_days, now)
    removed = 0
    while True:
        async with session_factory() as session:
            result = await session.execute(
                delete(SystemLog).where(SystemLog.occurred_at < cutoff).with_dialect_options(mysql_limit=DELETE_CHUNK)
            )
            await session.commit()
        count = result.rowcount or 0
        removed += count
        if count < DELETE_CHUNK:
            break
    if removed:
        logger.info("system log rows past retention removed", extra={"removed": removed, "retention_days": retention_days})
    return removed


def check_interval(retention_days: float) -> float:
    return max(MIN_CHECK_SECONDS, min(MAX_CHECK_SECONDS, retention_days * 86400 / 2))


async def purge_forever(session_factory: async_sessionmaker[AsyncSession], retention_days: float) -> None:
    interval = check_interval(retention_days)
    while True:
        try:
            await purge_expired(session_factory, retention_days)
        except (SQLAlchemyError, OSError):
            logger.warning("system log cleanup failed; will retry", exc_info=True)
        await asyncio.sleep(interval)
