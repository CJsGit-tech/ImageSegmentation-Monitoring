"""Real-time data history timings at simulator volume. Opt-in, because seeding takes a while.
HISTORY_VOLUME_DAYS sets the seeded span (default 92 days, about 1M system rows; 365 days is
about 4.2M, the simulator's yearly volume). Queries always cover the latest 3 months:

    sh tests/run_integration.sh env HISTORY_VOLUME=1 HISTORY_VOLUME_DAYS=365 \
        python -m pytest -q -s -p no:cacheprovider tests/test_history_volume.py

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

from app.auth.service import register_user
from app.core.config import load_settings
from app.core.time_range import add_months
from app.db.migrate import upgrade_to_head
from app.db.session import create_unpooled_engine, get_session
from app.history import service
from app.history.schemas import HistoryQuery
from app.main import create_app
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.record_import import RecordImport
from app.models.user import Role, User
from app.prestart import _wait_for_database
from app.records.pagination import order_by_clause

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DB_HOST") and os.environ.get("HISTORY_VOLUME")),
    reason="opt-in volume test: set HISTORY_VOLUME=1 and run through tests/run_integration.sh",
)

PASSWORD = "Str0ng!pass"
CATEGORIES = ("溫度", "壓力", "馬達轉速", "振動")
INTERVAL = timedelta(seconds=30)
DAYS = int(os.environ.get("HISTORY_VOLUME_DAYS", "92"))
START = datetime(2025, 1, 1)  # Naive UTC.
END = START + timedelta(days=DAYS)
MANUAL_EVERY = 20  # One manual record per 20 simulator steps (10 minutes), so the creator filter matters.
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


async def _seed(session, user_id):
    system, manual = 0, 0
    steps = int(timedelta(days=DAYS) / INTERVAL)
    rows = []

    async def flush():
        await session.execute(insert(Record), rows)
        await session.commit()
        rows.clear()

    for step in range(steps):
        timestamp = START + step * INTERVAL
        for index, category in enumerate(CATEGORIES):
            anomaly = (step * 4 + index) % 21 == 0  # About 5 % (PRD-07-R9), spread over categories.
            rows.append({
                "title": f"{category} 讀數",
                "value": (150.0 if anomaly else 50.0) + (step % 17) + index,
                "category": category,
                "timestamp": timestamp,
                "is_anomaly": anomaly,
                "created_by_user_id": None,
            })
            system += 1
        if step % MANUAL_EVERY == 0:
            rows.append({
                "title": "手動",
                "value": 1.0,
                "category": CATEGORIES[step % 4],
                "timestamp": timestamp,
                "is_anomaly": step % 40 == 0,
                "created_by_user_id": user_id,
            })
            manual += 1
        if len(rows) >= BATCH:
            await flush()
    if rows:
        await flush()
    return system, manual


async def _clear(session):
    await session.execute(delete(RecordImport))
    await session.execute(delete(Record))
    await session.execute(delete(CategorySetting))
    await session.execute(delete(User))
    await session.commit()


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
                await _clear(session)
                admin = await register_user(session, "admin1", PASSWORD)
                admin.role = Role.ADMIN
                await session.commit()
                return admin.id
        finally:
            await db.dispose()

    admin_id = asyncio.run(prepare())
    began = time.perf_counter()
    system, manual = run_in_db(lambda session: _seed(session, admin_id))
    print(f"\nseeded {system} system + {manual} manual rows over {DAYS} days in {time.perf_counter() - began:.1f} s")

    factory = async_sessionmaker(create_unpooled_engine(Env.settings), expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    http = TestClient(app, raise_server_exceptions=False)
    token = http.post("/api/v1/auth/login", json={"username": "admin1", "password": PASSWORD}).json()["access_token"]

    class Api:
        client = http
        headers = {"Authorization": f"Bearer {token}"}
        rows = system + manual

    yield Api
    run_in_db(_clear)


def timed(api, **params):
    began = time.perf_counter()
    response = api.client.get("/api/v1/history/records", headers=api.headers, params=params)
    elapsed = time.perf_counter() - began
    assert response.status_code == 200, response.text
    return response.json(), elapsed


def explain(statement):
    async def work(session):
        return [dict(row._mapping) for row in await session.execute(Explain(statement))]

    for row in run_in_db(work):
        print(f"    EXPLAIN table={row.get('table')} type={row.get('type')} key={row.get('key')} "
              f"rows={row.get('rows')} extra={row.get('Extra')}")


def test_timings_and_plans_for_the_latest_three_months(api):
    end = END.replace(tzinfo=UTC)
    start = _three_months_before(end)
    full = {"start": start.isoformat(), "end": end.isoformat()}
    cases = [
        ("all categories", {}),
        ("one category", {"category": "溫度"}),
        ("anomaly only", {"anomaly_only": "true"}),
        ("one category + anomaly only", {"category": "溫度", "anomaly_only": "true"}),
        ("one category + anomaly only, oldest first", {"category": "溫度", "anomaly_only": "true", "order": "asc"}),
        ("all categories, last hour", {"start": (end - timedelta(hours=1)).isoformat()}),
    ]
    print(f"\n{api.rows} rows in records; window {full['start']} .. {full['end']}")
    for label, extra in cases:
        params = {**full, "limit": 20, **extra}
        timed(api, **params)  # Warm the buffer pool, as a repeated screen query would.
        body, elapsed = timed(api, **params)
        print(f"  {label}: {elapsed * 1000:.0f} ms (total {body['total']})")
        query = HistoryQuery.model_validate(
            {**params, **({"category": [params["category"]]} if "category" in params else {})}
        )
        conditions = service.history_conditions(query)
        print("   count:")
        explain(select(func.count()).select_from(Record).where(*conditions))
        print("   page:")
        explain(select(Record).where(*conditions).order_by(*order_by_clause("timestamp", query.order)).limit(21))
        assert elapsed < CEILING_SECONDS
        assert all(item["is_system"] for item in body["items"])

    # Exactly 3 months at 4 rows per 30 s step; the seeded span stops one step before `end`.
    steps = int((end - start) / INTERVAL)
    assert timed(api, **full, limit=1)[0]["total"] == steps * len(CATEGORIES)

    # Keyset paging stays fast however far one walks.
    cursor, pages, began = None, 0, time.perf_counter()
    while pages < 30:
        body, _ = timed(api, **full, limit=100, **({"cursor": cursor} if cursor else {}))
        pages += 1
        cursor = body["next_cursor"]
    print(f"  30 pages of 100 walked in {(time.perf_counter() - began) * 1000:.0f} ms (each includes the count)")


def _three_months_before(end):
    # The same wall-clock time 3 months earlier: exactly the cap (the seeded spans end on day 1 to 3).
    start = end.replace(year=end.year if end.month > 3 else end.year - 1, month=(end.month - 4) % 12 + 1)
    assert add_months(start, 3) == end
    return start
