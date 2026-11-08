"""The running simulator: one tick per interval for every device, pushed live, then batch-written.

Runs as tasks inside the backend process (PRD-07-R6: no switch). Each tick judges every reading
once, with the range in force at that moment (PRD-09-R4), and the very same flag is pushed and
stored. Generated readings are never written to the system log (PRD-14-R6); only start, stop and
write failures are, as counts.

Only one simulator generates per database: it must hold a MariaDB named lock, so a second
backend process (or a second instance in tests) stays idle and takes over if the holder goes.
"""

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.realtime.hub import RealtimeHub, Reading, utc_now
from app.records.anomaly import CategoryRange, get_range, is_out_of_range
from app.records.service import build_record
from app.simulator.devices import DEVICES, Device
from app.simulator.generator import ValueModel

logger = logging.getLogger("app.simulator")

LOCK_NAME = "monitoring_dashboard_simulator"
FLUSH_SECONDS = 5.0
BATCH_SIZE = 200
BUFFER_LIMIT = 2000
MAX_RETRY_SECONDS = 30.0
SHUTDOWN_FLUSH_SECONDS = 5.0


class Simulator:
    def __init__(
        self,
        hub: RealtimeHub,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        interval_seconds: float,
        flush_seconds: float = FLUSH_SECONDS,
        batch_size: int = BATCH_SIZE,
        buffer_limit: int = BUFFER_LIMIT,
        rng: random.Random | None = None,
        devices: Iterable[Device] = DEVICES,
    ) -> None:
        self.hub = hub
        self.engine = engine
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self.flush_seconds = flush_seconds
        self.batch_size = batch_size
        self.buffer_limit = buffer_limit
        self.devices = tuple(devices)
        rng = rng or random.Random()
        self._models = {device.category: ValueModel(device, rng) for device in self.devices}
        self._ranges: dict[str, CategoryRange | None] | None = None
        self._buffer: list[Reading] = []
        self._flush_lock = asyncio.Lock()
        self._flush_now = asyncio.Event()
        self._lock_connection: AsyncConnection | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._write_failing = False
        self.generated_count = 0
        self.written_count = 0
        self.dropped_count = 0

    @property
    def is_leader(self) -> bool:
        return self._lock_connection is not None

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)

    # --- Lifecycle ---

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._generate_forever(), name="simulator-generate"),
            asyncio.create_task(self._write_forever(), name="simulator-write"),
        ]
        logger.info("simulator started", extra={"interval_seconds": self.interval_seconds})

    async def stop(self, timeout: float = SHUTDOWN_FLUSH_SECONDS) -> None:
        """Stops generating, then writes what is still buffered, waiting at most timeout seconds."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        self.hub.generator_running = False
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.flush(), timeout)
        lost = len(self._buffer)
        await self._release_lock()
        level = logging.WARNING if lost else logging.INFO
        logger.log(level, "simulator stopped", extra={"written": self.written_count, "unsaved_lost": lost})

    # --- Leadership ---

    async def _hold_lock(self) -> bool:
        """True while this instance holds the named lock; tries to take it when it does not."""
        try:
            if self._lock_connection is not None:
                still_held = await self._lock_connection.scalar(
                    select(func.is_used_lock(LOCK_NAME) == func.connection_id())
                )
                await self._lock_connection.commit()
                if still_held:
                    return True
                await self._release_lock()
            connection = await self.engine.connect()
            try:
                acquired = await connection.scalar(select(func.get_lock(LOCK_NAME, 0)))
                await connection.commit()
            except BaseException:
                await connection.close()
                raise
            if acquired == 1:
                self._lock_connection = connection
                logger.info("simulator is generating on this backend")
                return True
            await connection.close()
            return False
        except SQLAlchemyError:
            logger.warning("simulator could not check its database lock", exc_info=True)
            await self._release_lock(quietly=True)
            return False

    async def _release_lock(self, quietly: bool = False) -> None:
        connection, self._lock_connection = self._lock_connection, None
        if connection is None:
            return
        try:
            if not quietly:
                await connection.scalar(select(func.release_lock(LOCK_NAME)))
                await connection.commit()
        except SQLAlchemyError:
            pass
        finally:
            with contextlib.suppress(Exception):
                await connection.close()

    # --- Generation ---

    async def _generate_forever(self) -> None:
        next_at = time.monotonic()
        while True:
            leader = await self._hold_lock()
            self.hub.generator_running = leader
            if leader:
                await self.tick()
            next_at += self.interval_seconds
            now = time.monotonic()
            if next_at < now:
                # Fell behind (for example a slow database): skip missed ticks instead of bursting.
                next_at = now + self.interval_seconds
            await asyncio.sleep(next_at - now)

    async def _read_ranges(self) -> dict[str, CategoryRange | None] | None:
        try:
            async with self.session_factory() as session:
                ranges = {device.category: await get_range(session, device.category) for device in self.devices}
                await self.hub.refresh_display(session)
                # Ends the read snapshot, so the next tick sees an Admin's change.
                await session.commit()
            self._ranges = ranges
            return ranges
        except SQLAlchemyError:
            logger.warning("simulator could not read category ranges; using the last ones", exc_info=True)
            return self._ranges

    async def tick(self) -> list[Reading]:
        """One reading per device, all with the same generation time (PRD-07-R3)."""
        ranges = await self._read_ranges()
        if ranges is None:
            return []
        tick_at = utc_now()
        readings = []
        for device in self.devices:
            category_range = ranges[device.category]
            value = self._models[device.category].next_value(category_range)
            unit = self.hub.display_of(device.category).unit
            readings.append(
                Reading(
                    category=device.category,
                    title=device.title,
                    value=value,
                    unit=unit,
                    range=category_range,
                    is_anomaly=is_out_of_range(value, category_range),
                    timestamp=tick_at,
                )
            )
        self.generated_count += len(readings)
        self._enqueue(readings)
        self.hub.publish(readings, tick_at)
        return readings

    # --- Batch writing (PRD-07-R4) ---

    def _enqueue(self, readings: list[Reading]) -> None:
        self._buffer.extend(readings)
        overflow = len(self._buffer) - self.buffer_limit
        if overflow > 0:
            del self._buffer[:overflow]
            self.dropped_count += overflow
            logger.warning(
                "simulator buffer full; oldest unsaved readings dropped",
                extra={"dropped": overflow, "buffer_limit": self.buffer_limit},
            )
        if len(self._buffer) >= self.batch_size:
            self._flush_now.set()

    async def flush(self) -> bool:
        """Writes everything buffered in one transaction. False if the database refused it."""
        async with self._flush_lock:
            batch = list(self._buffer)
            if not batch:
                return True
            rows = [
                build_record(
                    title=reading.title,
                    value=reading.value,
                    category=reading.category,
                    timestamp=reading.timestamp,
                    creator=None,
                    is_anomaly=reading.is_anomaly,
                )
                for reading in batch
            ]
            try:
                async with self.session_factory() as session:
                    session.add_all(rows)
                    await session.commit()
            except (SQLAlchemyError, OSError):
                if not self._write_failing:
                    logger.warning(
                        "simulator batch write failed; keeping readings to retry",
                        exc_info=True,
                        extra={"buffered": len(self._buffer)},
                    )
                self._write_failing = True
                return False
            # Only what was written: readings added meanwhile stay for the next batch.
            written_ids = {id(reading) for reading in batch}
            self._buffer = [reading for reading in self._buffer if id(reading) not in written_ids]
            written = len(batch)
            self.written_count += written
            if self._write_failing:
                logger.info("simulator batch writes recovered", extra={"written": written})
                self._write_failing = False
            return True

    async def _write_forever(self) -> None:
        delay = self.flush_seconds
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._flush_now.wait(), delay)
            self._flush_now.clear()
            if await self.flush():
                delay = self.flush_seconds
            else:
                delay = min(MAX_RETRY_SECONDS, max(1.0, delay * 2))
