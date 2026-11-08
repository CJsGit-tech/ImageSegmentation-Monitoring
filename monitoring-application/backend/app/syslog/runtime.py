"""Starts and stops system log persistence with the application (FastAPI lifespan)."""

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.syslog.handler import AuditHandler, SystemErrorHandler
from app.syslog.retention import purge_forever
from app.syslog.writer import SyslogWriter

AUDIT_LOGGER = "app.audit"


@dataclass
class SyslogRuntime:
    writer: SyslogWriter
    audit_handler: AuditHandler
    error_handler: SystemErrorHandler
    retention_task: asyncio.Task[None]

    async def stop(self) -> None:
        self.retention_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.retention_task
        logging.getLogger(AUDIT_LOGGER).removeHandler(self.audit_handler)
        logging.getLogger().removeHandler(self.error_handler)
        await self.writer.stop()


async def start_syslog(session_factory: async_sessionmaker[AsyncSession], retention_days: float) -> SyslogRuntime:
    writer = SyslogWriter(session_factory)
    await writer.start()
    audit_handler = AuditHandler(writer.submit)
    error_handler = SystemErrorHandler(writer.submit)
    logging.getLogger(AUDIT_LOGGER).addHandler(audit_handler)
    logging.getLogger().addHandler(error_handler)
    retention_task = asyncio.create_task(purge_forever(session_factory, retention_days), name="syslog-retention")
    return SyslogRuntime(writer, audit_handler, error_handler, retention_task)
