"""Database status timings at volume. Opt-in, because seeding takes a while:

    sh tests/run_integration.sh env DBSTATUS_VOLUME=1 python -m pytest -q -s -p no:cacheprovider tests/test_db_status_volume.py

Seeds DBSTATUS_VOLUME_DAYS days of simulator output (default 92, about 1.06M rows; 365 is
about 4.2M) and prints the cost of a first check (exact count included), of cached ones, and the
plans of both queries.
"""

import asyncio
import os
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ClauseElement, Executable

from app.core.config import load_settings
from app.db.migrate import upgrade_to_head
from app.db.session import create_pooled_engine, create_unpooled_engine
from app.dbstatus.service import COUNT_STATEMENT, LAST_BATCH_STATEMENT, DbStatusMonitor
from app.models.record import Record
from app.models.record_import import RecordImport
from app.prestart import _wait_for_database

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DB_HOST") and os.environ.get("DBSTATUS_VOLUME")),
    reason="opt-in volume test: set DBSTATUS_VOLUME=1 and run through tests/run_integration.sh",
)


class Explain(Executable, ClauseElement):
    inherit_cache = False

    def __init__(self, statement):
        self.statement = statement


@compiles(Explain)
def _compile_explain(element, compiler, **kw):
    return "EXPLAIN " + compiler.process(element.statement, **kw)


# EXPLAIN is a test-only construct wrapping the application's own ORM statements.
STATEMENTS = {
    "last batch": LAST_BATCH_STATEMENT,
    "record count": COUNT_STATEMENT,
}


CATEGORIES = ("溫度", "壓力", "馬達轉速", "振動")
INTERVAL = timedelta(seconds=30)
START = datetime(2026, 1, 1)  # Naive UTC.
BATCH = 20_000
# Generous ceilings: the printed numbers are the evidence; these catch a broken plan.
FIRST_CHECK_CEILING = 10.0
CACHED_CHECK_CEILING = 0.5


async def _seed(engine, days):
    total, rows = 0, []
    async with AsyncSession(engine) as session:
        await session.execute(delete(RecordImport))
        await session.execute(delete(Record))
        await session.commit()
        for step in range(int(timedelta(days=days) / INTERVAL)):
            for category in CATEGORIES:
                rows.append({
                    "title": f"{category} 感測器",
                    "value": 50.0 + step % 17,
                    "category": category,
                    "timestamp": START + step * INTERVAL,
                    "is_anomaly": False,
                    "created_by_user_id": None,
                })
                if len(rows) == BATCH:
                    await session.execute(insert(Record), rows)
                    await session.commit()
                    total, rows = total + len(rows), []
        if rows:
            await session.execute(insert(Record), rows)
            await session.commit()
            total += len(rows)
    return total


def test_status_cost_at_volume():
    os.environ.update(
        DB_HOST=os.environ["TEST_DB_HOST"],
        DB_PORT=os.environ.get("TEST_DB_PORT", "3306"),
        DB_NAME=os.environ.get("TEST_DB_NAME", "monitoring_test"),
        DB_USER=os.environ.get("TEST_DB_USER", "monitoring"),
        DB_PASSWORD=os.environ["TEST_DB_PASSWORD"],
    )
    settings = load_settings()
    days = int(os.environ.get("DBSTATUS_VOLUME_DAYS", "92"))

    async def run():
        plain = create_unpooled_engine(settings)
        pooled = create_pooled_engine(settings)
        try:
            await _wait_for_database(plain, 90)
            await upgrade_to_head(plain)
            began = time.perf_counter()
            total = await _seed(plain, days)
            print(f"\nseeded {total} rows in {time.perf_counter() - began:.1f} s")

            async with plain.connect() as connection:
                for name, statement in STATEMENTS.items():
                    plan = (await connection.execute(Explain(statement))).mappings().all()
                    began = time.perf_counter()
                    value = await connection.scalar(statement)
                    print(f"  {name}: {(time.perf_counter() - began) * 1000:.1f} ms -> {value}")
                    for row in plan:
                        print("     ", {k: row[k] for k in ("table", "type", "key", "rows", "Extra")})
            # Generous probe limit so the first check waits for the whole count and reports it.
            monitor = DbStatusMonitor(probe_timeout=FIRST_CHECK_CEILING)
            capacity = settings.db_pool_size + settings.db_max_overflow
            began = time.perf_counter()
            first = await monitor.check(pooled, pool_capacity=capacity, simulator_interval_seconds=30)
            first_s = time.perf_counter() - began
            timings = []
            for _ in range(5):
                began = time.perf_counter()
                cached = await monitor.check(pooled, pool_capacity=capacity, simulator_interval_seconds=30)
                timings.append(time.perf_counter() - began)
            print(f"  first check (exact count): {first_s * 1000:.0f} ms, count={first.record_count}")
            print(f"  cached checks: {', '.join(f'{t * 1000:.1f}' for t in timings)} ms")
            print(f"  probe round trip: {cached.response_time_ms} ms, last batch {cached.last_batch_write_at}")
            assert first.status == "available"
            assert first.record_count == total
            assert first.last_batch_write_at == START + (int(timedelta(days=days) / INTERVAL) - 1) * INTERVAL
            assert first_s < FIRST_CHECK_CEILING
            assert max(timings) < CACHED_CHECK_CEILING
            async with AsyncSession(plain) as session:
                await session.execute(delete(Record))
                await session.commit()
        finally:
            await pooled.dispose()
            await plain.dispose()

    asyncio.run(run())
