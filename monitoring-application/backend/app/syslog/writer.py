"""Writes system log rows in batches, off the request path.

submit() only appends to a bounded in-memory buffer, from any thread. A task flushes it every
FLUSH_SECONDS (sooner once BATCH_SIZE rows wait) in its own short transaction of plain inserts:
no locking reads and no foreign keys, so it cannot hit error 1020 or wait on request locks.

A row leaves the buffer only after its insert commits. While the database is unavailable rows
stay buffered and the writer retries with backoff; past BUFFER_LIMIT the oldest are dropped,
with a warning on stdout. Every event is also on stdout, written by the logging call itself,
so a crash loses at most the last second of rows from the database, not from the log output.
"""

import asyncio
import contextlib
import logging
import threading
from collections import deque

from sqlalchemy import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.system_log import SystemLog
from app.syslog.handler import Row

logger = logging.getLogger("app.syslog")

FLUSH_SECONDS = 1.0
BATCH_SIZE = 100
INSERT_CHUNK = 500
BUFFER_LIMIT = 10_000
MAX_RETRY_SECONDS = 30.0
SHUTDOWN_FLUSH_SECONDS = 5.0


class SyslogWriter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        flush_seconds: float = FLUSH_SECONDS,
        batch_size: int = BATCH_SIZE,
        buffer_limit: int = BUFFER_LIMIT,
    ) -> None:
        self.session_factory = session_factory
        self.flush_seconds = flush_seconds
        self.batch_size = batch_size
        self.buffer_limit = buffer_limit
        self._buffer: deque[Row] = deque()
        self._lock = threading.Lock()
        self._flush_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._write_failing = False
        self._dropping = False
        self.written_count = 0
        self.dropped_count = 0

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)

    def submit(self, row: Row) -> None:
        """Called from logging handlers, possibly on another thread. Never blocks on I/O."""
        dropped = False
        with self._lock:
            self._buffer.append(row)
            if len(self._buffer) > self.buffer_limit:
                self._buffer.popleft()
                self.dropped_count += 1
                dropped = True
            ready = len(self._buffer) >= self.batch_size
        if dropped and not self._dropping:
            self._dropping = True
            logger.warning("system log buffer full; oldest unsaved rows dropped", extra={"buffer_limit": self.buffer_limit})
        if ready and self._loop is not None:
            with contextlib.suppress(RuntimeError):  # The loop already closed.
                self._loop.call_soon_threadsafe(self._wake.set)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._write_forever(), name="syslog-write")

    async def stop(self, timeout: float = SHUTDOWN_FLUSH_SECONDS) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.flush(), timeout)
        self._loop = None
        lost = len(self._buffer)
        logger.log(
            logging.WARNING if lost else logging.INFO,
            "system log writer stopped",
            extra={"written": self.written_count, "unsaved_lost": lost, "dropped": self.dropped_count},
        )

    async def flush(self) -> bool:
        """Writes everything buffered, in chunks. False if the database refused a chunk."""
        async with self._flush_lock:
            while True:
                with self._lock:
                    batch = [self._buffer[i] for i in range(min(INSERT_CHUNK, len(self._buffer)))]
                if not batch:
                    return True
                try:
                    async with self.session_factory() as session:
                        await session.execute(insert(SystemLog), batch)
                        await session.commit()
                except (SQLAlchemyError, OSError):
                    if not self._write_failing:
                        logger.warning(
                            "system log write failed; keeping rows to retry",
                            exc_info=True,
                            extra={"buffered": len(self._buffer)},
                        )
                    self._write_failing = True
                    return False
                written = {id(row) for row in batch}
                with self._lock:
                    # Overflow may have dropped some of them meanwhile; the rest are at the front.
                    while self._buffer and id(self._buffer[0]) in written:
                        self._buffer.popleft()
                # Warn again on the next overflow episode.
                self._dropping = False
                self.written_count += len(batch)
                if self._write_failing:
                    logger.info("system log writes recovered", extra={"written": len(batch)})
                    self._write_failing = False

    async def _write_forever(self) -> None:
        delay = self.flush_seconds
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), delay)
            self._wake.clear()
            if await self.flush():
                delay = self.flush_seconds
            else:
                delay = min(MAX_RETRY_SECONDS, max(1.0, delay * 2))
