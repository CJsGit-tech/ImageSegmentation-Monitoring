"""Analysis timings at the simulator's 3-month volume (about 1M rows). Opt-in, because seeding
takes a while:

    sh tests/run_integration.sh env ANALYTICS_VOLUME=1 python -m pytest -q -s -p no:cacheprovider tests/test_analytics_volume.py

Prints timings and the EXPLAIN plan of each query. EXPLAIN is a test-only construct that wraps
the application's own ORM statements; application code has no raw SQL.
"""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ClauseElement, Executable

from app.analytics import service
from app.analytics.schemas import AnalysisQuery
from app.auth.service import register_user
from app.core.config import load_settings
from app.db.migrate import upgrade_to_head
from app.db.session import create_unpooled_engine, get_session
from app.main import create_app
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.record_import import RecordImport
from app.models.user import Role, User
from app.prestart import _wait_for_database
from app.records.pagination import order_by_clause

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DB_HOST") and os.environ.get("ANALYTICS_VOLUME")),
    reason="opt-in volume test: set ANALYTICS_VOLUME=1 and run through tests/run_integration.sh",
)

PASSWORD = "Str0ng!pass"
CATEGORIES = ("溫度", "壓力", "馬達轉速", "振動")
INTERVAL = timedelta(seconds=30)
DAYS = 92
START = datetime(2026, 1, 1)  # Naive UTC.
BATCH = 20_000
# Generous ceilings: the printed numbers are the evidence; these catch a broken plan.
CEILING_SECONDS = 15.0


class Explain(Executable, ClauseElement):
    inherit_cache = False

    def __init__(self, statement):
        self.statement = statement


@compiles(Explain)
def _compile_explain(element, compiler, **kw):
    return "EXPLAIN " + compiler.process(element.statement, **kw)


class Env:
    settings = None


def run_in_db(work):
    async def runner():
        db = create_unpooled_engine(Env.settings)
        try:
            async with AsyncSession(db, expire_on_commit=False) as session:
                return await work(session)
        finally:
            await db.dispose()

    return asyncio.run(runner())


async def _seed(session):
    total, anomalies = 0, 0
    steps = int(timedelta(days=DAYS) / INTERVAL)
    rows = []
    for step in range(steps):
        timestamp = START + step * INTERVAL
        for index, category in enumerate(CATEGORIES):
            anomaly = (step * 4 + index) % 997 == 0  # About 0.1 %.
            anomalies += anomaly
            rows.append({
                "title": f"{category} 感測器",
                "value": (150.0 if anomaly else 50.0) + (step % 17) + index,
                "category": category,
                "timestamp": timestamp,
                "is_anomaly": anomaly,
                "created_by_user_id": None,
            })
            if len(rows) == BATCH:
                await session.execute(insert(Record), rows)
                await session.commit()
                total += len(rows)
                rows = []
    if rows:
        await session.execute(insert(Record), rows)
        await session.commit()
        total += len(rows)
    return total, anomalies


@pytest.fixture(scope="module")
def api():
    os.environ.update(
        DB_HOST=os.environ["TEST_DB_HOST"],
        DB_PORT=os.environ.get("TEST_DB_PORT", "3306"),
        DB_NAME=os.environ.get("TEST_DB_NAME", "monitoring_test"),
        DB_USER=os.environ.get("TEST_DB_USER", "monitoring"),
        DB_PASSWORD=os.environ["TEST_DB_PASSWORD"],
    )
    Env.settings = load_settings()

    async def prepare():
        db = create_unpooled_engine(Env.settings)
        try:
            await _wait_for_database(db, 90)
            await upgrade_to_head(db)
            async with AsyncSession(db, expire_on_commit=False) as session:
                await session.execute(delete(RecordImport))
                await session.execute(delete(Record))
                await session.execute(delete(CategorySetting))
                await session.execute(delete(User))
                await session.commit()
                user = await register_user(session, "viewer1", PASSWORD)
                user.role = Role.VIEWER
                await session.commit()
        finally:
            await db.dispose()

    asyncio.run(prepare())
    began = time.perf_counter()
    total, anomalies = run_in_db(_seed)
    print(f"\nseeded {total} rows ({anomalies} anomalies) in {time.perf_counter() - began:.1f} s")

    factory = async_sessionmaker(create_unpooled_engine(Env.settings), expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    http = TestClient(app, raise_server_exceptions=False)
    token = http.post("/api/v1/auth/login", json={"username": "viewer1", "password": PASSWORD}).json()["access_token"]

    class Api:
        client = http
        headers = {"Authorization": f"Bearer {token}"}
        rows = total

    yield Api

    async def clear(session):
        await session.execute(delete(Record))
        await session.execute(delete(User))
        await session.commit()

    run_in_db(clear)


FULL = {"start": "2026-01-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"}  # Exactly 3 months.


def timed(api, path, **params):
    began = time.perf_counter()
    response = api.client.get(path, headers=api.headers, params=params)
    elapsed = time.perf_counter() - began
    assert response.status_code == 200, response.text
    return response.json(), elapsed


def explain(statement):
    async def work(session):
        return [dict(row._mapping) for row in await session.execute(Explain(statement))]

    for row in run_in_db(work):
        print(f"    EXPLAIN table={row.get('table')} type={row.get('type')} key={row.get('key')} "
              f"rows={row.get('rows')} extra={row.get('Extra')}")


def test_timings_and_plans_at_three_month_volume(api):
    window = service.check_query(AnalysisQuery(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 4, 1, tzinfo=UTC)))
    one = service.check_query(AnalysisQuery(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 4, 1, tzinfo=UTC), category=["溫度"]))
    cases = [
        ("statistics, all categories, 3 months", "/api/v1/analytics/statistics", FULL, service.statistics_statement(window)),
        ("statistics, one category, 3 months", "/api/v1/analytics/statistics", {**FULL, "category": "溫度"}, service.statistics_statement(one)),
        ("trend hour, all categories, 3 months", "/api/v1/analytics/trend", {**FULL, "granularity": "hour"}, service.trend_statement(window, "hour")),
        ("trend day, all categories, 3 months", "/api/v1/analytics/trend", {**FULL, "granularity": "day"}, service.trend_statement(window, "day")),
        ("trend minute, all categories, 1 day", "/api/v1/analytics/trend",
         {"start": "2026-02-01T00:00:00+08:00", "end": "2026-02-02T00:00:00+08:00", "granularity": "minute"}, None),
        ("anomalies page 1, all categories, 3 months", "/api/v1/analytics/anomalies", {**FULL, "limit": 20},
         select(Record).where(*service.anomaly_conditions(window))
         .order_by(*order_by_clause("timestamp", "desc")).limit(21)),
        ("anomalies page 1, one category, 3 months", "/api/v1/analytics/anomalies", {**FULL, "limit": 20, "category": "溫度"},
         select(Record).where(*service.anomaly_conditions(one))
         .order_by(*order_by_clause("timestamp", "desc")).limit(21)),
    ]
    print(f"\n{api.rows} rows in records")
    for label, path, params, statement in cases:
        timed(api, path, **params)  # Warm the buffer pool, as a repeated screen query would.
        body, elapsed = timed(api, path, **params)
        print(f"  {label}: {elapsed * 1000:.0f} ms")
        if statement is not None:
            explain(statement)
        assert elapsed < CEILING_SECONDS
        if path.endswith("statistics") and "category" not in params:
            # Jan 1 00:00 to Apr 1 00:00 inclusive: 90 days of 30 s steps plus the closing instant.
            assert sum(row["count"] for row in body["categories"]) == (90 * 2880 + 1) * len(CATEGORIES)
        if path.endswith("trend") and params.get("granularity") == "hour":
            assert all(len(s["points"]) == 90 * 24 + 1 for s in body["categories"])  # Jan 1 to Apr 1 inclusive.

    print("  anomalies total count (per page):")
    explain(select(func.count()).select_from(Record).where(*service.anomaly_conditions(window)))

    # Paging deep into the anomaly list stays keyset-fast.
    cursor, pages, began = None, 0, time.perf_counter()
    while pages < 30:
        params = {**FULL, "limit": 20, **({"cursor": cursor} if cursor else {})}
        body, _ = timed(api, "/api/v1/analytics/anomalies", **params)
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    print(f"  anomalies: {pages} pages walked in {(time.perf_counter() - began) * 1000:.0f} ms")
