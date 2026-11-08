"""Excel export time, memory and file size at about 100k and about 1M detail rows (LB-332).
Opt-in, because seeding takes a while:

    sh tests/run_integration.sh env EXPORTS_VOLUME=1 python -m pytest -q -s -p no:cacheprovider tests/test_exports_volume.py

Seeds the simulator's 3-month volume (4 categories every 30 s, about 0.1 % anomalies), then
times, through the HTTP endpoint and in one process like the server:
- iter_records alone (LB-331's detail query and RecordOut construction), to show its share;
- the whole export request, with peak RSS sampled during it and the resulting file size.
"""

import asyncio
import io
import os
import resource
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics import service as analytics
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

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DB_HOST") and os.environ.get("EXPORTS_VOLUME")),
    reason="opt-in volume test: set EXPORTS_VOLUME=1 and run through tests/run_integration.sh",
)

PASSWORD = "Str0ng!pass"
CATEGORIES = ("溫度", "壓力", "馬達轉速", "振動")
INTERVAL = timedelta(seconds=30)
DAYS = 92
START = datetime(2026, 1, 1)  # Naive UTC.
BATCH = 20_000
CASES = (
    ("about 100k rows (9 days)", "2026-01-01T00:00:00+08:00", "2026-01-10T00:00:00+08:00"),
    ("about 1M rows (exactly 3 months)", "2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"),
)


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
    total, rows = 0, []
    for step in range(int(timedelta(days=DAYS) / INTERVAL)):
        timestamp = START + step * INTERVAL
        for index, category in enumerate(CATEGORIES):
            anomaly = (step * 4 + index) % 997 == 0
            rows.append({
                "title": f"{category} 感測器",
                "value": (150.0 if anomaly else 50.0) + (step % 17) + index + 0.125,
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
    return total


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
                user = await register_user(session, "viewer1", PASSWORD)
                user.role = Role.VIEWER
                await session.commit()
        finally:
            await db.dispose()

    asyncio.run(prepare())
    began = time.perf_counter()
    total = run_in_db(_seed)
    print(f"\nseeded {total} rows in {time.perf_counter() - began:.1f} s")

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

    yield Api
    run_in_db(_clear)


def rss_mb():
    with open("/proc/self/status") as status:
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


class PeakRss:
    """Samples this process's RSS every 50 ms while the block runs."""

    def __enter__(self):
        self.base = self.peak = rss_mb()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def _sample(self):
        while not self._stop.wait(0.05):
            self.peak = max(self.peak, rss_mb())

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, rss_mb())


def time_iter_records(start, end):
    async def work(session):
        query = AnalysisQuery(start=start, end=end)
        count, began = 0, time.perf_counter()
        async for _ in analytics.iter_records(session, query, batch_size=5000):
            count += 1
        return count, time.perf_counter() - began

    return run_in_db(work)


@pytest.mark.parametrize(("label", "start", "end"), CASES)
def test_export_time_memory_and_size(api, label, start, end):
    statistics = api.client.get("/api/v1/analytics/statistics", headers=api.headers, params={"start": start, "end": end}).json()
    expected = sum(item["count"] for item in statistics["categories"])

    iterated, iter_seconds = time_iter_records(start, end)
    assert iterated == expected

    with PeakRss() as memory:
        began = time.perf_counter()
        response = api.client.get("/api/v1/analytics/export", headers=api.headers, params={"start": start, "end": end})
        seconds = time.perf_counter() - began
    assert response.status_code == 200, response.text[:500]
    size_mb = len(response.content) / 1024 / 1024

    # Row count from the file itself; the full read-back is done for the smaller case only.
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert not [name for name in archive.namelist() if "chart" in name or "drawing" in name]
    book = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True)
    detail = book["明細"]
    if expected < 200_000:
        rows = sum(1 for _ in detail.iter_rows(min_row=2, values_only=True))
        marked = sum(1 for row in detail.iter_rows(min_row=2, values_only=True) if row[5] == "異常")
        print(f"  read back: {rows} detail rows, {marked} marked anomalous")
        assert rows == expected
    else:
        assert detail.max_row - 1 == expected  # From the sheet's <dimension>.
    book.close()

    print(
        f"  {label}: {expected} detail rows | export {seconds:.1f} s "
        f"(iter_records alone {iter_seconds:.1f} s, {iter_seconds / seconds:.0%}) | "
        f"file {size_mb:.1f} MB | RSS {memory.base:.0f} -> peak {memory.peak:.0f} MB "
        f"(+{memory.peak - memory.base:.0f} MB, includes the response body held by the test client) | "
        f"ru_maxrss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB"
    )
