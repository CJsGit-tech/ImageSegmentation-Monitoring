"""Checks the database for the Admin status screen (PRD-15), cheaply enough to run every 10 s.

Each call takes one connection from the application's own pool, so it sees what the rest of the
backend sees, and times a trivial select on it. Everything that touches the database is bounded
by PROBE_TIMEOUT_SECONDS, so a hung database cannot hang the request. A failure after the caller
was authenticated is reported as status "unavailable" with a fixed error category; the database's
own error text never leaves this module, since it can quote bound values.

The exact record count can take about a second at millions of rows, so it is cached for
COUNT_CACHE_SECONDS and refreshed by one background task per process, however many Admin screens
are open. A refresh is never cancelled halfway, so no query is left running on the server.

DB-down episodes are not written to the system log from here: the simulator already logs its
failed batch writes, and other endpoints' database errors are logged by the error handler. This
module only prints state changes at INFO, which the system log does not keep.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import func, literal, select
from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import get_engine
from app.dbstatus.schemas import DbError, DbStatus, DbWarning, ErrorCategory, PoolUsage
from app.models.record import Record

logger = logging.getLogger("app.dbstatus")

REFRESH_INTERVAL_SECONDS = 10  # PRD-15-R5
PROBE_TIMEOUT_SECONDS = 2.0
COUNT_CACHE_SECONDS = 60.0
# Engineering choices (PRD-15 leaves thresholds open). A local MariaDB answers in a few ms.
SLOW_RESPONSE_MS = 250.0
POOL_HIGH_RATIO = 0.8
BATCH_STALE_INTERVALS = 3

ERROR_MESSAGES: dict[ErrorCategory, str] = {
    "timeout": "資料庫沒有在時限內回應",
    "unreachable": "無法連線到資料庫",
    "auth_failed": "資料庫拒絕後端的登入",
    "pool_exhausted": "後端的資料庫連線已全部占用",
    "error": "資料庫發生錯誤",
}
# MariaDB / MySQL client and server error numbers.
_AUTH_CODES = frozenset({1044, 1045, 1698})
_UNREACHABLE_CODES = frozenset({2002, 2003, 2005, 2006, 2013, 2055, 1040, 1152, 1153, 1158, 1159, 1160, 1161})


# The timestamp of the newest system record, i.e. of the simulator's last batch (PRD-07-R4): only
# the simulator writes records without a creator, in time order. MAX(id) for a NULL creator is read
# from the end of the creator foreign-key index ("Select tables optimized away"), then one primary
# key lookup; MAX(timestamp) with the same filter scans the whole table (1M rows: about 350 ms).
LAST_BATCH_STATEMENT = select(Record.timestamp).where(
    Record.id == select(func.max(Record.id)).where(Record.created_by_user_id.is_(None)).scalar_subquery()
)

# Exact, from every source. InnoDB counts by scanning its smallest index.
COUNT_STATEMENT = select(func.count()).select_from(Record)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _driver_code(exc: BaseException) -> int | None:
    orig = exc.orig if isinstance(exc, DBAPIError) else exc
    args = getattr(orig, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


def categorize(exc: BaseException, *, pool_full: bool) -> ErrorCategory:
    if isinstance(exc, PoolTimeoutError) or (isinstance(exc, TimeoutError) and pool_full):
        return "pool_exhausted"
    if isinstance(exc, TimeoutError):
        return "timeout"
    code = _driver_code(exc)
    if code in _AUTH_CODES:
        return "auth_failed"
    orig = exc.orig if isinstance(exc, DBAPIError) else exc
    if code in _UNREACHABLE_CODES or isinstance(exc, InterfaceError) or isinstance(orig, OSError):
        return "unreachable"
    return "error"


def pool_usage(engine: AsyncEngine, capacity: int) -> PoolUsage:
    pool = engine.pool
    try:
        in_use, idle = int(pool.checkedout()), int(pool.checkedin())  # type: ignore[attr-defined]
    except AttributeError:  # A pool without counters, such as NullPool.
        in_use, idle = 0, 0
    capacity = max(capacity, 1)
    return PoolUsage(
        in_use=in_use, idle=idle, open=in_use + idle, capacity=capacity, usage_ratio=round(in_use / capacity, 4)
    )


class DbStatusMonitor:
    """One per process: remembers the last success and caches the record count."""

    def __init__(
        self,
        *,
        probe_timeout: float = PROBE_TIMEOUT_SECONDS,
        count_cache_seconds: float = COUNT_CACHE_SECONDS,
    ) -> None:
        self.probe_timeout = probe_timeout
        self.count_cache_seconds = count_cache_seconds
        self.last_available_at: datetime | None = None
        self._was_available: bool | None = None
        self._count: int | None = None
        self._count_as_of: datetime | None = None
        self._count_taken: float | None = None
        self._count_task: asyncio.Task[None] | None = None

    async def check(self, engine: AsyncEngine, *, pool_capacity: int, simulator_interval_seconds: float) -> DbStatus:
        checked_at = utc_now()
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.probe_timeout):
                response_ms, last_batch = await self._probe(engine)
        except Exception as exc:
            pool = pool_usage(engine, pool_capacity)
            category = categorize(exc, pool_full=pool.in_use >= pool.capacity)
            if category == "error":
                logger.warning("database status check failed", extra={"exception": type(exc).__name__})
            self._note(available=False, category=category)
            return DbStatus(
                status="unavailable",
                checked_at=checked_at,
                last_available_at=self.last_available_at,
                error=DbError(category=category, message=ERROR_MESSAGES[category]),
                response_time_ms=None,
                pool=pool,
                record_count=None,
                record_count_as_of=None,
                last_batch_write_at=None,
                warnings=[],
                refresh_interval_seconds=REFRESH_INTERVAL_SECONDS,
            )

        self.last_available_at = checked_at
        self._note(available=True)
        remaining = self.probe_timeout - (time.monotonic() - started)
        record_count, record_count_as_of = await self._record_count(engine, remaining)
        # Sampled after the probe's connection went back, so it shows the rest of the backend.
        pool = pool_usage(engine, pool_capacity)
        return DbStatus(
            status="available",
            checked_at=checked_at,
            last_available_at=self.last_available_at,
            error=None,
            response_time_ms=response_ms,
            pool=pool,
            record_count=record_count,
            record_count_as_of=record_count_as_of,
            last_batch_write_at=last_batch,
            warnings=self._warnings(response_ms, pool, last_batch, checked_at, simulator_interval_seconds),
            refresh_interval_seconds=REFRESH_INTERVAL_SECONDS,
        )

    async def _probe(self, engine: AsyncEngine) -> tuple[float, datetime | None]:
        async with engine.connect() as connection:
            began = time.perf_counter()
            await connection.execute(select(literal(1)))
            response_ms = round((time.perf_counter() - began) * 1000, 2)
            last_batch = await connection.scalar(LAST_BATCH_STATEMENT)
        return response_ms, last_batch

    async def _record_count(self, engine: AsyncEngine, wait_seconds: float) -> tuple[int | None, datetime | None]:
        fresh = self._count_taken is not None and time.monotonic() - self._count_taken < self.count_cache_seconds
        if not fresh:
            task = self._count_task
            # A task from another event loop (tests start one per client) can never finish here.
            if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
                self._count_task = asyncio.create_task(self._refresh_count(engine), name="dbstatus-record-count")
            if self._count is None and wait_seconds > 0:
                # asyncio.wait never cancels the task, so a slow count still finishes for the next call.
                await asyncio.wait({self._count_task}, timeout=wait_seconds)
        return self._count, self._count_as_of

    async def _refresh_count(self, engine: AsyncEngine) -> None:
        try:
            async with engine.connect() as connection:
                count = await connection.scalar(COUNT_STATEMENT)
        except Exception as exc:
            logger.info("record count not refreshed", extra={"exception": type(exc).__name__})
            return
        self._count, self._count_as_of, self._count_taken = int(count or 0), utc_now(), time.monotonic()

    def _warnings(
        self,
        response_ms: float,
        pool: PoolUsage,
        last_batch: datetime | None,
        checked_at: datetime,
        simulator_interval_seconds: float,
    ) -> list[DbWarning]:
        warnings = []
        if response_ms >= SLOW_RESPONSE_MS:
            warnings.append(DbWarning(code="SLOW_RESPONSE", message=f"回應時間超過 {SLOW_RESPONSE_MS:.0f} ms"))
        if pool.usage_ratio >= POOL_HIGH_RATIO:
            warnings.append(DbWarning(code="POOL_HIGH", message=f"連線池使用超過 {POOL_HIGH_RATIO:.0%}"))
        stale_after = BATCH_STALE_INTERVALS * simulator_interval_seconds
        if last_batch is not None and (checked_at - last_batch).total_seconds() > stale_after:
            warnings.append(DbWarning(code="BATCH_STALE", message=f"超過 {stale_after:.0f} 秒沒有新的批次寫入"))
        return warnings

    def _note(self, *, available: bool, category: ErrorCategory | None = None) -> None:
        if available == self._was_available:
            return
        if available:
            if self._was_available is not None:
                logger.info("database status: available again")
        else:
            logger.info("database status: unavailable", extra={"category": category})
        self._was_available = available


@lru_cache
def get_monitor() -> DbStatusMonitor:
    return DbStatusMonitor()


def get_status_engine() -> AsyncEngine:
    """The application's pooled engine, so the pool numbers are the ones requests use."""
    return get_engine()
