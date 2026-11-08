"""Real-time data history checks (LB-336, PRD-16) against a real MariaDB. Run through tests/run_integration.sh."""

import asyncio
import os
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

GOOD_PASSWORD = "Str0ng!pass"
LOGIN = "/api/v1/auth/login"
RECORDS = "/api/v1/records"
IMPORTS = "/api/v1/records/imports"
HISTORY = "/api/v1/history/records"
TAIPEI = timezone(timedelta(hours=8))
ITEM_KEYS = {"id", "title", "value", "category", "timestamp", "creator_id", "creator_name", "is_system", "is_anomaly"}

DAY = datetime(2026, 3, 10)  # Naive UTC.
DAY_START = DAY.replace(tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)


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


def get(api, who="admin1", **params):
    return api.http.get(HISTORY, headers=api.headers[who] if who else {}, params=params)


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


def walk(api, limit, **params):
    rows, cursor = [], None
    for _ in range(5000):
        query = {**params, "limit": limit}
        if cursor:
            query["cursor"] = cursor
        body = ok(get(api, **query))
        rows.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            assert body["total"] == len(rows)
            return rows
    raise AssertionError("too many pages")


RANGE = {"start": iso(DAY_START), "end": iso(DAY_END)}


def test_only_system_records_appear_never_manual_or_imported(client):
    system_ids = seed([("lb-a", 1.0, DAY + timedelta(hours=1)), ("lb-a", 2.0, DAY + timedelta(hours=2), True)])
    seed([("lb-a", 3.0, DAY + timedelta(hours=3), False, client.ids["user1"])])  # Manual, seeded.
    manual = client.http.post(
        RECORDS,
        headers=client.headers["admin1"],
        json={"title": "手動", "value": 10, "category": "lb-a", "timestamp": iso(DAY_START + timedelta(hours=4))},
    )
    assert manual.status_code == 201, manual.text
    content = f"title,value,category,timestamp\n導入,30,lb-a,{iso(DAY_START + timedelta(hours=5))}\n"
    imported = client.http.post(
        IMPORTS,
        headers=client.headers["user1"],
        files={"file": (f"lb336-{uuid4().hex}.csv", content.encode(), "application/octet-stream")},
    )
    assert imported.status_code == 201, imported.text

    body = ok(get(client, **RANGE))
    assert body["total"] == 2 and body["next_cursor"] is None
    assert [item["id"] for item in body["items"]] == list(reversed(system_ids))  # Newest first.
    for item in body["items"]:
        assert set(item) == ITEM_KEYS
        assert item["creator_id"] is None and item["creator_name"] == "系統" and item["is_system"] is True
    newest = body["items"][0]
    assert (newest["title"], newest["value"], newest["category"], newest["is_anomaly"]) == ("t-1", 2.0, "lb-a", True)
    assert newest["timestamp"] == "2026-03-10T02:00:00Z"


def test_pages_cover_every_record_once_in_both_orders(client):
    # Pairs share a timestamp, so the id tie-break is exercised.
    rows = [(f"lb-{i % 2}", float(i), DAY + timedelta(minutes=i // 2)) for i in range(37)]
    rows.append(("lb-0", 1.0, DAY + timedelta(days=3)))  # Outside the range.
    ids = seed(rows)
    for order, reverse in (("desc", True), ("asc", False)):
        items = walk(client, 5, order=order, **RANGE)
        got = [item["id"] for item in items]
        assert len(got) == len(set(got)) == 37 and set(got) == set(ids[:37])
        keys = [(item["timestamp"], item["id"]) for item in items]
        assert keys == sorted(keys, reverse=reverse)


def test_records_added_while_paging_do_not_shift_pages(client):
    seed([("lb-a", float(i), DAY + timedelta(minutes=i)) for i in range(10)])
    first = ok(get(client, limit=4, **RANGE))
    seed([("lb-a", 99.0, DAY + timedelta(minutes=30))])  # Newer than every row already shown.
    second = ok(get(client, limit=4, cursor=first["next_cursor"], **RANGE))
    assert [item["value"] for item in first["items"]] == [9.0, 8.0, 7.0, 6.0]
    assert [item["value"] for item in second["items"]] == [5.0, 4.0, 3.0, 2.0]


def test_filters_combine(client):
    rows = [
        ("lb-a", 1.0, DAY + timedelta(hours=1)),
        ("lb-a", 2.0, DAY + timedelta(hours=2), True),
        ("lb-b", 3.0, DAY + timedelta(hours=3), True),
        ("lb-b", 4.0, DAY + timedelta(hours=4)),
        ("lb-c", 5.0, DAY + timedelta(hours=5), True),
        ("lb-a", 6.0, DAY + timedelta(days=2), True),  # Outside the range.
    ]
    seed(rows)

    def values(**params):
        return sorted(item["value"] for item in walk(client, 2, **params))

    assert values(**RANGE) == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert values(category="lb-a", **RANGE) == [1.0, 2.0]
    assert values(category=["lb-a", "lb-b"], **RANGE) == [1.0, 2.0, 3.0, 4.0]
    assert values(anomaly_only="true", **RANGE) == [2.0, 3.0, 5.0]
    assert values(category="lb-b", anomaly_only="true", **RANGE) == [3.0]
    narrow = {"start": iso(DAY_START + timedelta(hours=2)), "end": iso(DAY_START + timedelta(hours=4))}
    assert values(category="lb-b", **narrow) == [3.0, 4.0]
    assert values(category="lb-a", anomaly_only="true", **narrow) == [2.0]


def test_both_edges_are_included_and_offsets_respected(client):
    seed([("lb-a", 1.0, DAY), ("lb-a", 2.0, DAY + timedelta(hours=1)), ("lb-a", 3.0, DAY + timedelta(hours=1, microseconds=1))])
    start = DAY_START.astimezone(TAIPEI)
    body = ok(get(client, start=iso(start), end=iso(start + timedelta(hours=1))))
    assert [item["value"] for item in body["items"]] == [2.0, 1.0]
    point = ok(get(client, start=iso(DAY_START), end=iso(DAY_START)))
    assert [item["value"] for item in point["items"]] == [1.0]


def test_three_months_exactly_is_allowed_and_one_second_more_is_refused(client):
    start = datetime(2026, 1, 1, tzinfo=TAIPEI)
    exact = datetime(2026, 4, 1, tzinfo=TAIPEI)
    seed([("lb-a", 1.0, datetime(2026, 3, 31, 15, 59, 59))])  # 2026-03-31 23:59:59 in Taipei.
    assert ok(get(client, start=iso(start), end=iso(exact)))["total"] == 1
    error = assert_error(get(client, start=iso(start), end=iso(exact + timedelta(seconds=1))), 422, "RANGE_TOO_LONG")
    assert error["details"]["max_months"] == 3
    assert error["details"]["latest_allowed_end"] == iso(exact)
    assert "3 個月" in error["message"]


def test_time_range_is_required_and_must_not_be_reversed(client):
    assert_error(get(client), 422, "VALIDATION_ERROR")
    assert_error(get(client, start=RANGE["start"]), 422, "VALIDATION_ERROR")
    assert_error(get(client, start="2026-03-10T00:00:00", end=RANGE["end"]), 422, "VALIDATION_ERROR")  # No offset.
    error = assert_error(get(client, start=RANGE["end"], end=RANGE["start"]), 422, "VALIDATION_ERROR")
    assert error["details"][0]["type"] == "range_reversed"
    assert_error(get(client, sort_by="value", **RANGE), 422, "VALIDATION_ERROR")  # Timestamp order only.
    assert_error(get(client, limit=101, **RANGE), 422, "VALIDATION_ERROR")


def test_cursor_from_another_order_or_garbage_is_refused(client):
    seed([("lb-a", float(i), DAY + timedelta(minutes=i)) for i in range(3)])
    cursor = ok(get(client, limit=1, **RANGE))["next_cursor"]
    error = assert_error(get(client, limit=1, cursor=cursor, order="asc", **RANGE), 422, "VALIDATION_ERROR")
    assert error["details"][0]["field"] == "query.cursor"
    assert_error(get(client, limit=1, cursor="not-a-cursor", **RANGE), 422, "VALIDATION_ERROR")


def test_empty_range_returns_an_empty_page(client):
    seed([("lb-a", 1.0, DAY - timedelta(days=5))])
    body = ok(get(client, **RANGE))
    assert body == {"items": [], "total": 0, "limit": 20, "next_cursor": None}


def test_admin_edited_system_record_stays_with_its_latest_content(client):
    (record_id,) = seed([("lb-a", 1.0, DAY + timedelta(hours=1))])
    patched = client.http.patch(
        f"{RECORDS}/{record_id}", headers=client.headers["admin1"], json={"title": "改過", "value": 7.5}
    )
    assert patched.status_code == 200, patched.text
    (item,) = ok(get(client, **RANGE))["items"]
    assert (item["id"], item["title"], item["value"], item["creator_name"], item["is_system"]) == (
        record_id, "改過", 7.5, "系統", True
    )


@pytest.mark.parametrize("who", ("user1", "viewer1"))
def test_user_and_viewer_are_refused(client, who):
    seed([("lb-a", 1.0, DAY + timedelta(hours=1))])
    assert_error(get(client, who=who, **RANGE), 403, "FORBIDDEN")


def test_without_a_session_the_request_is_refused(client):
    response = get(client, who=None, **RANGE)
    assert response.status_code == 401, response.text


def test_contract_has_no_export_and_only_a_read(client):
    paths = {path: methods for path, methods in client.http.get("/openapi.json").json()["paths"].items() if path.startswith("/api/v1/history")}
    assert paths.keys() == {HISTORY} and set(paths[HISTORY]) == {"get"}
