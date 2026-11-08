"""Data analysis checks (LB-331, PRD-10, PRD-11) against a real MariaDB. Run through tests/run_integration.sh."""

import asyncio
import os
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from alembic import command
from sqlalchemy import delete, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics import service
from app.analytics.schemas import AnalysisQuery
from app.auth.service import register_user
from app.core.config import load_settings
from app.db.migrate import alembic_config, upgrade_to_head
from app.db.session import create_unpooled_engine, get_session
from app.main import create_app
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.record_import import RecordImport
from app.models.user import Role, User
from app.prestart import _wait_for_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

GOOD_PASSWORD = "Str0ng!pass"
LOGIN = "/api/v1/auth/login"
RECORDS = "/api/v1/records"
IMPORTS = "/api/v1/records/imports"
STATISTICS = "/api/v1/analytics/statistics"
TREND = "/api/v1/analytics/trend"
ANOMALIES = "/api/v1/analytics/anomalies"
ENDPOINTS = (STATISTICS, TREND, ANOMALIES)
ROLES = ("admin1", "user1", "viewer1")
TAIPEI = timezone(timedelta(hours=8))
STAT_KEYS = {"category", "unit", "count", "sum", "avg", "max", "min"}


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


def seed(rows):
    """rows: (category, value, naive UTC timestamp[, is_anomaly[, creator_id]])."""

    async def work(session):
        records = [
            Record(
                title=f"t-{index}",
                value=row[1],
                category=row[0],
                timestamp=row[2],
                is_anomaly=row[3] if len(row) > 3 else False,
                created_by_user_id=row[4] if len(row) > 4 else None,
            )
            for index, row in enumerate(rows)
        ]
        session.add_all(records)
        await session.commit()
        return [record.id for record in records]

    return run_in_db(work)


def set_category(category, range_min=None, range_max=None, unit=None):
    async def work(session):
        await session.merge(CategorySetting(category=category, range_min=range_min, range_max=range_max, unit=unit))
        await session.commit()

    run_in_db(work)


async def _clear(session):
    await session.execute(delete(RecordImport))
    await session.execute(delete(Record))
    await session.execute(delete(CategorySetting))
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
                await session.execute(delete(User))
                await session.commit()
                for username, role in (("admin1", Role.ADMIN), ("user1", Role.USER), ("viewer1", Role.VIEWER)):
                    user = await register_user(session, username, GOOD_PASSWORD)
                    user.role = role
                await session.commit()
        finally:
            await db.dispose()

    asyncio.run(prepare())
    factory = async_sessionmaker(create_unpooled_engine(Env.settings), expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override

    class Api:
        http = TestClient(app, raise_server_exceptions=False)
        headers = {}
        ids = {}

    Api.ids = run_in_db(lambda session: _ids(session))
    yield Api

    async def teardown(session):
        await _clear(session)
        await session.execute(delete(User))
        await session.commit()

    run_in_db(teardown)


async def _ids(session):
    return {user.username: user.id for user in await session.scalars(select(User))}


@pytest.fixture
def client(api):
    run_in_db(_clear)
    for username in api.ids:
        response = api.http.post(LOGIN, json={"username": username, "password": GOOD_PASSWORD})
        assert response.status_code == 200, response.text
        api.headers[username] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    yield api
    run_in_db(_clear)


def get(api, path, who="viewer1", **params):
    return api.http.get(path, headers=api.headers[who] if who else {}, params=params)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code, response.text
    return error


def iso(value):
    return value.isoformat()


def walk(api, path, limit, **params):
    rows, cursor = [], None
    for _ in range(5000):
        query = {**params, "limit": limit}
        if cursor:
            query["cursor"] = cursor
        body = ok(get(api, path, **query))
        rows.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            assert body["total"] == len(rows)
            return rows
    raise AssertionError("too many pages")


DAY = datetime(2026, 3, 10)  # Naive UTC.
DAY_START = DAY.replace(tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)


# --- Statistics (PRD-10) ---


def test_statistics_match_hand_computed_values_per_category_without_a_total_row(client):
    set_category("lb-a", unit="°C")
    seed([
        ("lb-a", 1.5, DAY + timedelta(hours=1)),
        ("lb-a", 2.5, DAY + timedelta(hours=2)),
        ("lb-a", 10.0, DAY + timedelta(hours=3)),
        ("lb-a", -4.0, DAY + timedelta(hours=4), True),
        ("lb-b", 100.0, DAY + timedelta(hours=5)),
        ("lb-b", 200.0, DAY + timedelta(hours=6)),
        ("lb-a", 999.0, DAY - timedelta(hours=1)),  # Outside the range.
        ("lb-b", 999.0, DAY + timedelta(days=2)),
    ])
    body = ok(get(client, STATISTICS, start=iso(DAY_START), end=iso(DAY_END)))
    assert body["has_data"] is True and body["category"] is None
    rows = body["categories"]
    assert [row["category"] for row in rows] == ["lb-a", "lb-b"]  # One row each, no total row.
    assert all(set(row) == STAT_KEYS for row in rows)
    assert rows[0] == {"category": "lb-a", "unit": "°C", "count": 4, "sum": 10.0, "avg": 2.5, "max": 10.0, "min": -4.0}
    assert rows[1] == {"category": "lb-b", "unit": None, "count": 2, "sum": 300.0, "avg": 150.0, "max": 200.0, "min": 100.0}


def test_both_edges_are_inclusive_with_offsets(client):
    start = datetime(2026, 3, 10, 8, 0, tzinfo=TAIPEI)  # 00:00 UTC.
    end = datetime(2026, 3, 10, 20, 0, tzinfo=TAIPEI)  # 12:00 UTC.
    micro = timedelta(microseconds=1)
    seed([
        ("lb-a", 1.0, DAY),
        ("lb-a", 2.0, DAY + timedelta(hours=12)),
        ("lb-a", 50.0, DAY - micro),
        ("lb-a", 60.0, DAY + timedelta(hours=12) + micro),
    ])
    row = ok(get(client, STATISTICS, start=iso(start), end=iso(end)))["categories"][0]
    assert (row["count"], row["sum"], row["min"], row["max"]) == (2, 3.0, 1.0, 2.0)
    records = walk(client, RECORDS, 100, time_from=iso(start), time_to=iso(end))
    assert sorted(r["value"] for r in records) == [1.0, 2.0]
    anomalies_seen = ok(get(client, ANOMALIES, start=iso(start), end=iso(end)))
    assert anomalies_seen["total"] == 0


def test_start_equal_to_end_is_one_instant(client):
    seed([("lb-a", 7.0, DAY), ("lb-a", 8.0, DAY + timedelta(microseconds=1))])
    body = ok(get(client, STATISTICS, start=iso(DAY_START), end=iso(DAY_START)))
    assert body["categories"][0]["count"] == 1 and body["categories"][0]["sum"] == 7.0
    trend = ok(get(client, TREND, start=iso(DAY_START), end=iso(DAY_START), granularity="minute"))
    assert [p["count"] for p in trend["categories"][0]["points"]] == [1]


def test_selected_categories_get_a_row_each_and_match_case_sensitively(client):
    seed([("lb-a", 1.0, DAY + timedelta(hours=1)), ("lb-b", 5.0, DAY + timedelta(hours=1)), ("LB-A", 3.0, DAY + timedelta(hours=1))])
    body = ok(get(client, STATISTICS, start=iso(DAY_START), end=iso(DAY_END), category=["lb-b", "lb-none", "lb-a", " lb-b "]))
    assert body["category"] == ["lb-b", "lb-none", "lb-a"]
    assert [(r["category"], r["count"], r["sum"]) for r in body["categories"]] == [
        ("lb-b", 1, 5.0),
        ("lb-none", 0, None),
        ("lb-a", 1, 1.0),
    ]
    none_row = body["categories"][1]
    assert none_row["avg"] is None and none_row["max"] is None and none_row["min"] is None
    trend = ok(get(client, TREND, start=iso(DAY_START), end=iso(DAY_END), category=["lb-none"]))
    assert trend["has_data"] is False and trend["categories"][0]["points"] == []


def test_category_counts_add_up_to_the_records_list_total(client):
    seed([(f"lb-{i % 3}", float(i), DAY + timedelta(minutes=i)) for i in range(37)])
    params = {"start": iso(DAY_START), "end": iso(DAY_END)}
    rows = ok(get(client, STATISTICS, **params))["categories"]
    total = ok(get(client, RECORDS, time_from=params["start"], time_to=params["end"], limit=1))["total"]
    assert sum(r["count"] for r in rows) == total == 37
    for row in rows:
        per_category = ok(get(client, RECORDS, category=row["category"], time_from=params["start"], time_to=params["end"], limit=1))
        assert per_category["total"] == row["count"]


def test_empty_state_is_not_zeros(client):
    seed([("lb-a", 1.0, DAY - timedelta(days=5))])
    params = {"start": iso(DAY_START), "end": iso(DAY_END)}
    stats = ok(get(client, STATISTICS, **params))
    assert stats["has_data"] is False and stats["categories"] == []
    trend = ok(get(client, TREND, **params))
    assert trend["has_data"] is False and trend["categories"] == []
    anomalies = ok(get(client, ANOMALIES, **params))
    assert anomalies["items"] == [] and anomalies["total"] == 0 and anomalies["next_cursor"] is None


@pytest.mark.parametrize("path", ENDPOINTS)
def test_reversed_range_is_refused(client, path):
    response = get(client, path, start="2026-03-02T00:00:00+08:00", end="2026-03-01T23:59:59+08:00")
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"] == [{"field": "query.end", "message": "結束時間不可早於開始時間", "type": "range_reversed"}]


@pytest.mark.parametrize(
    "params, field",
    [
        ({"start": "2026-03-01T00:00:00", "end": "2026-03-02T00:00:00Z"}, "query.start"),
        ({"start": "1700000000", "end": "2026-03-02T00:00:00Z"}, "query.start"),
        ({"end": "2026-03-02T00:00:00Z"}, "query.start"),
        ({"start": "2026-03-01T00:00:00Z", "end": "2026-03-02T00:00:00Z", "category": " "}, "query.category.0"),
        ({"start": "2026-03-01T00:00:00Z", "end": "2026-03-02T00:00:00Z", "title": "x"}, "query.title"),
    ],
)
def test_bad_parameters_are_refused(client, params, field):
    error = assert_error(get(client, STATISTICS, **params), 422, "VALIDATION_ERROR")
    assert field in {problem["field"] for problem in error["details"]}


def test_bad_granularity_is_refused(client):
    error = assert_error(get(client, TREND, start=iso(DAY_START), end=iso(DAY_END), granularity="week"), 422, "VALIDATION_ERROR")
    assert error["details"][0]["field"] == "query.granularity"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_exactly_three_months_is_allowed_and_one_second_more_is_refused(client, path):
    extra = {"granularity": "day"} if path == TREND else {}
    seed([("lb-a", 4.0, datetime(2026, 2, 27, 16, 0), True)])  # 2026-02-28 00:00 +08:00, the last instant.
    allowed = ok(get(client, path, start="2025-11-30T00:00:00+08:00", end="2026-02-28T00:00:00+08:00", **extra))
    if path == STATISTICS:
        assert allowed["categories"][0]["count"] == 1
    refused = get(client, path, start="2025-11-30T00:00:00+08:00", end="2026-02-28T00:00:01+08:00", **extra)
    error = assert_error(refused, 422, "RANGE_TOO_LONG")
    assert error["message"] == "時間範圍超過 3 個月上限。請縮短範圍，或分批查詢。"
    assert error["details"] == {"max_months": 3, "latest_allowed_end": "2026-02-28T00:00:00+08:00"}


def test_adjacent_three_month_windows_each_show_only_their_own_records(client):
    seed([
        ("lb-a", 1.0, datetime(2026, 1, 15)),
        ("lb-a", 2.0, datetime(2026, 4, 15)),
        ("lb-a", 3.0, datetime(2026, 7, 15)),
    ])
    first = ok(get(client, STATISTICS, start="2026-01-01T00:00:00Z", end="2026-04-01T00:00:00Z"))
    second = ok(get(client, STATISTICS, start="2026-04-01T00:00:00.000001Z", end="2026-07-01T00:00:00Z"))
    third = ok(get(client, STATISTICS, start="2026-07-01T00:00:00.000001Z", end="2026-10-01T00:00:00Z"))
    assert [b["categories"][0]["sum"] for b in (first, second, third)] == [1.0, 2.0, 3.0]
    assert all(b["categories"][0]["count"] == 1 for b in (first, second, third))


def test_generated_manual_and_imported_records_are_all_counted(client):
    seed([("lb-src", 10.0, DAY + timedelta(hours=1))])  # System generated (no creator).
    manual = client.http.post(
        RECORDS,
        headers=client.headers["user1"],
        json={"title": "手動", "value": 20.0, "category": "lb-src", "timestamp": iso(DAY_START + timedelta(hours=2))},
    )
    assert manual.status_code == 201, manual.text
    content = f"title,value,category,timestamp\n導入,30,lb-src,{iso(DAY_START + timedelta(hours=3))}\n"
    imported = client.http.post(
        IMPORTS,
        headers=client.headers["user1"],
        files={"file": (f"lb331-{uuid4().hex}.csv", content.encode(), "application/octet-stream")},
    )
    assert imported.status_code == 201, imported.text
    row = ok(get(client, STATISTICS, start=iso(DAY_START), end=iso(DAY_END)))["categories"][0]
    assert (row["count"], row["sum"]) == (3, 60.0)


@pytest.mark.parametrize("who", ROLES)
def test_every_role_can_use_analysis(client, who):
    seed([("lb-a", 1.0, DAY + timedelta(hours=1), True)])
    params = {"start": iso(DAY_START), "end": iso(DAY_END)}
    for path in ENDPOINTS:
        assert get(client, path, who=who, **params).status_code == 200


@pytest.mark.parametrize("path", ENDPOINTS)
def test_unauthenticated_is_refused(client, path):
    assert_error(get(client, path, who=None, start=iso(DAY_START), end=iso(DAY_END)), 401, "UNAUTHORIZED")


# --- Trend (PRD-11) ---


def _local_bucket(timestamp_text, seconds, zone):
    local = datetime.fromisoformat(timestamp_text).astimezone(zone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    offset = (local - midnight) // timedelta(seconds=seconds) * seconds
    return midnight + timedelta(seconds=offset)


@pytest.mark.parametrize("granularity, seconds", [("minute", 60), ("hour", 3600), ("day", 86400)])
def test_trend_points_match_the_records_list(client, granularity, seconds):
    rows = []
    for i in range(300):  # Every 37 s for about 3 hours, crossing local midnight (16:00 UTC).
        anomaly = i % 41 == 0
        rows.append(("lb-t", 50.0 + (i * 7) % 13 + (100 if anomaly else 0), datetime(2026, 3, 10, 14, 30) + timedelta(seconds=37 * i), anomaly))
    rows.append(("lb-other", 1.0, datetime(2026, 3, 10, 15, 0)))
    seed(rows)
    start, end = "2026-03-10T22:00:00+08:00", "2026-03-11T02:00:00+08:00"
    body = ok(get(client, TREND, start=start, end=end, category="lb-t", granularity=granularity))
    assert (body["bucket_seconds"], body["utc_offset"], body["granularity"]) == (seconds, "+08:00", granularity)
    [series] = body["categories"]
    records = walk(client, RECORDS, 100, category="lb-t", time_from=start, time_to=end)
    expected = {}
    for record in records:
        expected.setdefault(_local_bucket(record["timestamp"], seconds, TAIPEI), []).append(record)
    assert [datetime.fromisoformat(p["bucket_start"]) for p in series["points"]] == sorted(expected)
    for point in series["points"]:
        assert point["bucket_start"].endswith("+08:00")
        members = expected[datetime.fromisoformat(point["bucket_start"])]
        values = [m["value"] for m in members]
        anomalous = [m["value"] for m in members if m["is_anomaly"]]
        assert point["count"] == len(values)
        assert point["avg"] == pytest.approx(sum(values) / len(values))
        assert (point["min"], point["max"]) == (min(values), max(values))
        assert point["anomaly_count"] == len(anomalous)
        assert (point["anomaly_min"], point["anomaly_max"]) == ((min(anomalous), max(anomalous)) if anomalous else (None, None))
    assert sum(p["count"] for p in series["points"]) == len(records)


def test_day_buckets_start_at_local_midnight(client):
    seed([("lb-d", 1.0, datetime(2026, 3, 10, 15, 59)), ("lb-d", 3.0, datetime(2026, 3, 10, 16, 0))])
    body = ok(get(client, TREND, start="2026-03-10T00:00:00+08:00", end="2026-03-12T00:00:00+08:00", granularity="day"))
    points = body["categories"][0]["points"]
    assert [(p["bucket_start"], p["avg"]) for p in points] == [
        ("2026-03-10T00:00:00+08:00", 1.0),
        ("2026-03-11T00:00:00+08:00", 3.0),
    ]


def test_too_many_points_is_refused_before_querying_and_names_coarser_choices(client):
    error = assert_error(
        get(client, TREND, start="2026-03-01T00:00:00+08:00", end="2026-03-04T00:00:00+08:00", granularity="minute"),
        422,
        "TOO_MANY_POINTS",
    )
    assert error["message"] == "所選粒度在此時間範圍會產生過多資料點，請改選較粗的粒度。"
    assert error["details"] == {"granularity": "minute", "points_per_category": 4321, "max_points": 2500, "coarser": ["hour", "day"]}
    # 2,461 minute buckets (41 h) is within the limit; 42 h (2,521) is not.
    ok(get(client, TREND, start="2026-03-01T00:00:00Z", end="2026-03-02T17:00:00Z", granularity="minute"))
    assert_error(get(client, TREND, start="2026-03-01T00:00:00Z", end="2026-03-02T18:00:00Z", granularity="minute"), 422, "TOO_MANY_POINTS")
    # Hour over exactly 3 months fits, and is the default.
    body = ok(get(client, TREND, start="2025-11-30T00:00:00+08:00", end="2026-02-28T00:00:00+08:00"))
    assert body["granularity"] == "hour" and body["label"] == "每小時" and body["max_points_per_category"] == 2500


def test_anomalies_are_kept_in_averaged_buckets_with_range_lines(client):
    set_category("lb-r", 0.0, 90.0, "kPa")
    rows = [("lb-r", 50.0, DAY + timedelta(seconds=30 * i)) for i in range(119)]
    rows.append(("lb-r", 99.0, DAY + timedelta(minutes=30), True))
    rows.append(("lb-u", 5.0, DAY + timedelta(minutes=5)))
    seed(rows)
    body = ok(get(client, TREND, start=iso(DAY_START), end=iso(DAY_START + timedelta(hours=1)), granularity="hour"))
    by_name = {s["category"]: s for s in body["categories"]}
    series = by_name["lb-r"]
    assert series["range"] == {"min": 0.0, "max": 90.0} and series["range_status"] == "set" and series["unit"] == "kPa"
    [point] = series["points"]
    assert point["count"] == 120 and point["avg"] < 90.0  # The average alone would hide it.
    assert (point["anomaly_count"], point["anomaly_min"], point["anomaly_max"]) == (1, 99.0, 99.0)
    assert by_name["lb-u"]["range"] is None and by_name["lb-u"]["range_status"] == "unset"


# --- Anomaly list (PRD-10-R11) ---


def test_anomaly_list_pages_without_duplicates_or_gaps(client):
    rows = []
    for i in range(25):
        # Pairs share a timestamp, so the id tie-break is exercised.
        rows.append((f"lb-{i % 2}", 100.0 + i, DAY + timedelta(minutes=i // 2), True))
    rows += [(f"lb-{i % 2}", 1.0, DAY + timedelta(minutes=i)) for i in range(40)]
    rows.append(("lb-0", 500.0, DAY + timedelta(days=4), True))  # Outside the range.
    ids = seed(rows)
    expected = set(ids[:25])
    params = {"start": iso(DAY_START), "end": iso(DAY_END)}
    items = walk(client, ANOMALIES, 4, **params)
    got = [item["id"] for item in items]
    assert len(got) == len(set(got)) == 25 and set(got) == expected
    assert all(item["is_anomaly"] for item in items)
    keys = [(item["timestamp"], item["id"]) for item in items]
    assert keys == sorted(keys, reverse=True)  # Newest first.
    assert set(items[0]) == {"id", "title", "value", "category", "timestamp", "creator_id", "creator_name", "is_system", "is_anomaly"}

    only_one = walk(client, ANOMALIES, 5, category="lb-1", **params)
    assert {item["category"] for item in only_one} == {"lb-1"} and len(only_one) == 12

    by_value = walk(client, ANOMALIES, 7, sort_by="value", order="asc", **params)
    assert [item["value"] for item in by_value] == sorted(100.0 + i for i in range(25))


def test_anomaly_cursor_from_another_sort_is_refused(client):
    seed([("lb-a", float(i), DAY + timedelta(minutes=i), True) for i in range(3)])
    params = {"start": iso(DAY_START), "end": iso(DAY_END)}
    cursor = ok(get(client, ANOMALIES, limit=1, **params))["next_cursor"]
    error = assert_error(get(client, ANOMALIES, limit=1, cursor=cursor, order="asc", **params), 422, "VALIDATION_ERROR")
    assert error["details"][0]["field"] == "query.cursor"


# --- Python API for the Excel export (LB-332) ---


def test_iter_records_yields_every_counted_record_in_time_order(client):
    seed([(f"lb-{i % 2}", float(i), DAY + timedelta(seconds=i * 10), i % 7 == 0) for i in range(23)]
         + [("lb-0", 5.0, DAY + timedelta(days=3))])
    query = AnalysisQuery(start=DAY_START, end=DAY_END)

    async def work(session):
        stats = await service.category_statistics(session, query)
        records = [record async for record in service.iter_records(session, query, batch_size=4)]
        return stats, records

    stats, records = run_in_db(work)
    assert len(records) == sum(row.count for row in stats.categories) == 23
    assert [(r.timestamp, r.id) for r in records] == sorted((r.timestamp, r.id) for r in records)
    assert sum(r.is_anomaly for r in records) == 4
    assert all(r.creator_name == "系統" for r in records)


def test_python_api_raises_the_same_errors(client):
    from app.core.time_range import RangeTooLongError

    with pytest.raises(RangeTooLongError):
        service.check_query(AnalysisQuery(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 4, 1, 0, 0, 1, tzinfo=UTC)))
    window = service.check_query(AnalysisQuery(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 4, 1, tzinfo=UTC)))
    with pytest.raises(service.TooManyPointsError):
        service.check_granularity(window, "minute")


# --- Migration 0007 ---


def test_analytics_index_migration_downgrades_and_upgrades(client):
    async def indexes(connection):
        return await connection.run_sync(lambda sync: {index["name"] for index in inspect(sync).get_indexes("records")})

    async def check():
        engine = create_unpooled_engine(Env.settings)
        try:
            async with engine.connect() as connection:
                assert "ix_records_category_timestamp_value" in await indexes(connection)

            def downgrade(sync):
                config = alembic_config()
                config.attributes["connection"] = sync
                command.downgrade(config, "0006")

            async with engine.begin() as connection:
                await connection.run_sync(downgrade)
            async with engine.connect() as connection:
                remaining = await indexes(connection)
            assert "ix_records_category_timestamp_value" not in remaining
            assert {"ix_records_category_timestamp", "ix_records_is_anomaly_timestamp"} <= remaining
            await upgrade_to_head(engine)
            async with engine.connect() as connection:
                assert "ix_records_category_timestamp_value" in await indexes(connection)
        finally:
            await engine.dispose()

    asyncio.run(check())
