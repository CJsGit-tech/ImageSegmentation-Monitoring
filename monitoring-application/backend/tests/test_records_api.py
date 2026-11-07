"""Record API checks against a real MariaDB. Run through tests/run_integration.sh."""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.service import register_user
from app.core.config import load_settings
from app.db.migrate import upgrade_to_head
from app.db.session import create_unpooled_engine, get_session
from app.main import create_app
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.user import Role, User
from app.prestart import _wait_for_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

GOOD_PASSWORD = "Str0ng!pass"
LOGIN = "/api/v1/auth/login"
RECORDS = "/api/v1/records"
CATEGORIES = "/api/v1/records/categories"
BASE_TIME = datetime(2026, 1, 1)
VALID = {"title": "溫度巡檢", "value": 61.4, "category": "溫度"}


class Env:
    """The module's database settings. conftest replaces env vars around each test."""

    settings = None


def engine():
    return create_unpooled_engine(Env.settings)


def run_in_db(work):
    async def runner():
        db = engine()
        try:
            async with AsyncSession(db, expire_on_commit=False) as session:
                return await work(session)
        finally:
            await db.dispose()

    return asyncio.run(runner())


def seed(*rows, creator_id=None):
    """Adds rows directly, for values and creators the API cannot produce. Returns ids."""

    async def work(session):
        records = [
            Record(
                title=row.get("title", "t"),
                value=row.get("value", 1.0),
                category=row.get("category", "c"),
                timestamp=row.get("timestamp", BASE_TIME),
                created_by_user_id=row.get("creator_id", creator_id),
            )
            for row in rows
        ]
        session.add_all(records)
        await session.commit()
        return [record.id for record in records]

    return run_in_db(work)


def count_records():
    return run_in_db(lambda session: session.scalar(select(func.count()).select_from(Record)))


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
        db = engine()
        try:
            await _wait_for_database(db, 90)
            await upgrade_to_head(db)
            async with AsyncSession(db, expire_on_commit=False) as session:
                await session.execute(delete(Record))
                await session.execute(delete(User))
                await session.commit()
                for username, role in (
                    ("admin1", Role.ADMIN),
                    ("user1", Role.USER),
                    ("user2", Role.USER),
                    ("viewer1", Role.VIEWER),
                ):
                    user = await register_user(session, username, GOOD_PASSWORD)
                    user.role = role
                await session.commit()
        finally:
            await db.dispose()

    asyncio.run(prepare())

    factory = async_sessionmaker(engine(), expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    client = TestClient(app, raise_server_exceptions=False)

    class Api:
        http = client
        headers = {}
        ids = {}

    async def load_ids(session):
        return {user.username: user.id for user in await session.scalars(select(User))}

    Api.ids = run_in_db(load_ids)
    yield Api

    async def clear(session):
        await session.execute(delete(Record))
        await session.execute(delete(User))
        await session.commit()

    run_in_db(clear)


@pytest.fixture
def client(api):
    """Clean table and fresh logins, because other test modules may log the accounts in."""

    async def clear(session):
        await session.execute(delete(Record))
        await session.commit()

    run_in_db(clear)
    for username in api.ids:
        response = api.http.post(LOGIN, json={"username": username, "password": GOOD_PASSWORD})
        assert response.status_code == 200, response.text
        api.headers[username] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    yield api
    run_in_db(clear)


def call(api, method, path, who=None, **kwargs):
    headers = api.headers[who] if who else {}
    return api.http.request(method, path, headers=headers, **kwargs)


def create(api, who="user1", **fields):
    response = call(api, "POST", RECORDS, who, json={**VALID, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    return error


def error_fields(response):
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    return {problem["field"] for problem in response.json()["error"]["details"]}


def walk(api, limit=2, who="viewer1", **params):
    """Follows next_cursor to the end. Returns every row and asserts total stays right."""
    rows, cursor, pages = [], None, 0
    while True:
        query = {**params, "limit": limit}
        if cursor:
            query["cursor"] = cursor
        response = call(api, "GET", RECORDS, who, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        rows.extend(body["items"])
        pages += 1
        assert pages < 5000
        cursor = body["next_cursor"]
        if cursor is None:
            assert body["total"] == len(rows)
            return rows


def ids_of(rows):
    return [row["id"] for row in rows]


def utc(value):
    return datetime.fromisoformat(value).astimezone(UTC).replace(tzinfo=None)


# --- Create ---


@pytest.mark.parametrize("who", ["user1", "admin1"])
def test_create_appears_in_list_with_current_user_as_creator(client, who):
    created = create(client, who)
    assert created["title"] == VALID["title"]
    assert created["value"] == VALID["value"]
    assert created["category"] == VALID["category"]
    assert created["creator_id"] == client.ids[who]
    assert created["creator_name"] == who
    assert created["is_system"] is False
    assert created["is_anomaly"] is False

    listed = call(client, "GET", RECORDS, "viewer1").json()
    assert listed["total"] == 1
    assert listed["items"] == [created]
    assert call(client, "GET", f"{RECORDS}/{created['id']}", "viewer1").json() == created


@pytest.mark.parametrize("omit", [True, False])
def test_omitted_timestamp_is_the_creation_time(client, omit):
    fields = {} if omit else {"timestamp": None}
    before = datetime.now(UTC).replace(tzinfo=None)
    created = create(client, **fields)
    after = datetime.now(UTC).replace(tzinfo=None)
    assert created["timestamp"].endswith("Z")
    assert before <= utc(created["timestamp"]) <= after


def test_supplied_timestamp_keeps_the_instant_and_microseconds(client):
    created = create(client, timestamp="2026-09-01T00:14:42.123456+08:00")
    assert created["timestamp"] == "2026-08-31T16:14:42.123456Z"


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59.999999+00:00"])
def test_extreme_timestamps_round_trip(client, value):
    created = create(client, timestamp=value)
    assert utc(created["timestamp"]) == utc(value)


@pytest.mark.parametrize(
    "fields, field",
    [
        ({"title": None}, "body.title"),
        ({"title": ""}, "body.title"),
        ({"title": "   "}, "body.title"),
        ({"title": "x" * 256}, "body.title"),
        ({"title": "a\nb"}, "body.title"),
        ({"title": 5}, "body.title"),
        ({"value": None}, "body.value"),
        ({"value": "12"}, "body.value"),
        ({"value": "abc"}, "body.value"),
        ({"value": True}, "body.value"),
        ({"value": [1]}, "body.value"),
        ({"category": ""}, "body.category"),
        ({"category": "x" * 101}, "body.category"),
        ({"category": "a\x00b"}, "body.category"),
        ({"timestamp": "2026-09-01T00:14:42"}, "body.timestamp"),
        ({"timestamp": "2026-09-01"}, "body.timestamp"),
        ({"timestamp": "yesterday"}, "body.timestamp"),
        ({"timestamp": 1700000000}, "body.timestamp"),
        ({"timestamp": "1700000000"}, "body.timestamp"),
        ({"timestamp": "0001-01-01T00:00:00+08:00"}, "body.timestamp"),
        ({"timestamp": "9999-12-31T23:59:59-08:00"}, "body.timestamp"),
        ({"unit": "°C"}, "body.unit"),
        ({"creator_id": 1}, "body.creator_id"),
        ({"is_anomaly": True}, "body.is_anomaly"),
        ({"id": 1}, "body.id"),
    ],
)
def test_create_rejects_and_names_the_field(client, fields, field):
    response = call(client, "POST", RECORDS, "user1", json={**VALID, **fields})
    assert field in error_fields(response)
    assert count_records() == 0


@pytest.mark.parametrize("field", ["title", "value", "category"])
def test_create_requires_field(client, field):
    body = {key: value for key, value in VALID.items() if key != field}
    assert f"body.{field}" in error_fields(call(client, "POST", RECORDS, "user1", json=body))


@pytest.mark.parametrize("raw", ['{"title":"t","value":NaN,"category":"c"}', '{"title":"t","value":1e999,"category":"c"}'])
def test_create_rejects_non_finite_numbers(client, raw):
    response = client.http.post(
        RECORDS, content=raw, headers={**client.headers["user1"], "Content-Type": "application/json"}
    )
    assert "body.value" in error_fields(response)


def test_create_rejects_unpaired_surrogate(client):
    response = client.http.post(
        RECORDS,
        content='{"title":"\\ud800","value":1,"category":"c"}',
        headers={**client.headers["user1"], "Content-Type": "application/json"},
    )
    assert "body.title" in error_fields(response)


def test_integer_and_large_values_and_emoji_are_accepted(client):
    created = create(client, value=1798, title="😀 title", category="類別😀")
    assert created["value"] == 1798
    assert created["title"] == "😀 title"
    assert created["category"] == "類別😀"
    assert create(client, value=-1.7e308)["value"] == -1.7e308


def test_text_is_trimmed(client):
    created = create(client, title="  a  ", category="　b ")
    assert (created["title"], created["category"]) == ("a", "b")


# --- Permissions ---


@pytest.mark.parametrize(
    "method, path, kwargs",
    [
        ("POST", RECORDS, {"json": VALID}),
        ("GET", RECORDS, {}),
        ("GET", CATEGORIES, {}),
        ("GET", f"{RECORDS}/1", {}),
        ("PATCH", f"{RECORDS}/1", {"json": {"title": "x"}}),
        ("DELETE", f"{RECORDS}/1", {}),
    ],
)
def test_unauthenticated_is_rejected(client, method, path, kwargs):
    assert_error(call(client, method, path, **kwargs), 401, "UNAUTHORIZED")


def test_viewer_cannot_change_anything_but_can_read(client):
    record = create(client)
    assert_error(call(client, "POST", RECORDS, "viewer1", json=VALID), 403, "FORBIDDEN")
    path = f"{RECORDS}/{record['id']}"
    assert_error(call(client, "PATCH", path, "viewer1", json={"title": "x"}), 403, "FORBIDDEN")
    assert_error(call(client, "DELETE", path, "viewer1"), 403, "FORBIDDEN")
    # A record that does not exist gives the same answer, so ids cannot be probed.
    assert_error(call(client, "DELETE", f"{RECORDS}/999999", "viewer1"), 403, "FORBIDDEN")
    assert call(client, "GET", RECORDS, "viewer1").status_code == 200
    assert call(client, "GET", CATEGORIES, "viewer1").status_code == 200
    assert call(client, "GET", path, "viewer1").json() == record


def test_user_cannot_change_someone_elses_record(client):
    record = create(client, "user1")
    path = f"{RECORDS}/{record['id']}"
    error = assert_error(call(client, "PATCH", path, "user2", json={"title": "x"}), 403, "NOT_RECORD_OWNER")
    assert "建立者或 Admin" in error["message"]
    assert_error(call(client, "DELETE", path, "user2"), 403, "NOT_RECORD_OWNER")
    assert call(client, "GET", path, "user2").json() == record


def test_user_cannot_change_system_record_but_admin_can(client):
    [record_id] = seed({"title": "sys"})
    path = f"{RECORDS}/{record_id}"
    shown = call(client, "GET", path, "viewer1").json()
    assert (shown["creator_id"], shown["creator_name"], shown["is_system"]) == (None, "系統", True)

    assert_error(call(client, "PATCH", path, "user1", json={"title": "x"}), 403, "NOT_RECORD_OWNER")
    assert_error(call(client, "DELETE", path, "user1"), 403, "NOT_RECORD_OWNER")

    updated = call(client, "PATCH", path, "admin1", json={"title": "x"})
    assert updated.status_code == 200
    assert (updated.json()["title"], updated.json()["creator_id"], updated.json()["creator_name"]) == ("x", None, "系統")
    assert call(client, "DELETE", path, "admin1").status_code == 204


def test_admin_changes_anyones_record_and_creator_stays(client):
    record = create(client, "user1")
    path = f"{RECORDS}/{record['id']}"
    updated = call(client, "PATCH", path, "admin1", json={"value": 2.5}).json()
    assert updated["value"] == 2.5
    assert (updated["creator_id"], updated["creator_name"]) == (client.ids["user1"], "user1")
    assert call(client, "DELETE", path, "admin1").status_code == 204


# --- Update and delete ---


def test_owner_updates_all_fields_and_only_the_latest_is_kept(client):
    record = create(client, "user1")
    path = f"{RECORDS}/{record['id']}"
    changes = {"title": "新", "value": 9.5, "category": "壓力", "timestamp": "2026-01-02T03:04:05+00:00"}
    response = call(client, "PATCH", path, "user1", json=changes)
    assert response.status_code == 200
    updated = response.json()
    assert (updated["title"], updated["value"], updated["category"]) == ("新", 9.5, "壓力")
    assert updated["timestamp"] == "2026-01-02T03:04:05Z"
    assert updated["creator_id"] == record["creator_id"]
    assert call(client, "GET", RECORDS, "viewer1").json()["items"] == [updated]
    assert count_records() == 1


def test_partial_update_leaves_other_fields(client):
    record = create(client, timestamp="2026-01-02T03:04:05Z")
    updated = call(client, "PATCH", f"{RECORDS}/{record['id']}", "user1", json={"value": 7}).json()
    assert updated == {**record, "value": 7}


@pytest.mark.parametrize(
    "body, field",
    [
        ({"value": "abc"}, "body.value"),
        ({"value": None}, "body.value"),
        ({"title": ""}, "body.title"),
        ({"title": None}, "body.title"),
        ({"category": None}, "body.category"),
        ({"timestamp": None}, "body.timestamp"),
        ({"timestamp": "2026-01-01T00:00:00"}, "body.timestamp"),
        ({"creator_id": 1}, "body.creator_id"),
        ({"created_by_user_id": 1}, "body.created_by_user_id"),
        ({"unit": "rpm"}, "body.unit"),
    ],
)
def test_update_rejects_and_names_the_field(client, body, field):
    record = create(client)
    response = call(client, "PATCH", f"{RECORDS}/{record['id']}", "user1", json=body)
    assert field in error_fields(response)
    assert call(client, "GET", f"{RECORDS}/{record['id']}", "user1").json() == record


def test_update_requires_a_field(client):
    record = create(client)
    assert error_fields(call(client, "PATCH", f"{RECORDS}/{record['id']}", "user1", json={}))


def test_delete_is_permanent(client):
    record = create(client, "user1", category="only-here")
    path = f"{RECORDS}/{record['id']}"
    response = call(client, "DELETE", path, "user1")
    assert response.status_code == 204
    assert response.content == b""
    assert count_records() == 0
    assert call(client, "GET", RECORDS, "viewer1").json()["items"] == []
    assert call(client, "GET", CATEGORIES, "viewer1").json()["items"] == []


@pytest.mark.parametrize("record_id", ["999999", "-1", "0", "99999999999999999999999"])
def test_missing_record_explains_itself(client, record_id):
    path = f"{RECORDS}/{record_id}"
    for response in (
        call(client, "GET", path, "user1"),
        call(client, "PATCH", path, "user1", json={"title": "x"}),
        call(client, "DELETE", path, "user1"),
    ):
        error = assert_error(response, 404, "RECORD_NOT_FOUND")
        assert error["message"] == "這筆記錄已不存在"


def test_non_numeric_id_is_rejected(client):
    assert "path.record_id" in error_fields(call(client, "GET", f"{RECORDS}/abc", "user1"))


def test_acting_on_a_deleted_record_explains_itself(client):
    record = create(client)
    path = f"{RECORDS}/{record['id']}"
    assert call(client, "DELETE", path, "user1").status_code == 204
    assert_error(call(client, "DELETE", path, "user1"), 404, "RECORD_NOT_FOUND")
    assert_error(call(client, "PATCH", path, "user1", json={"title": "x"}), 404, "RECORD_NOT_FOUND")


def test_concurrent_deletes_give_one_success_and_one_not_found(client):
    from app.records.errors import RecordNotFoundError
    from app.records.service import delete_record

    [record_id] = seed({"title": "t"})

    async def attempt(db):
        async with AsyncSession(db, expire_on_commit=False) as session:
            admin = await session.scalar(select(User).where(User.username == "admin1"))
            try:
                await delete_record(session, admin, record_id)
                return "deleted"
            except RecordNotFoundError:
                return "missing"

    async def race():
        db = engine()
        try:
            return await asyncio.gather(*(attempt(db) for _ in range(4)))
        finally:
            await db.dispose()

    assert sorted(asyncio.run(race())) == ["deleted", "missing", "missing", "missing"]


def test_concurrent_updates_all_succeed_and_update_racing_delete_never_fails(client):
    from app.records.errors import RecordNotFoundError
    from app.records.schemas import RecordUpdate
    from app.records.service import delete_record, update_record

    [record_id] = seed({"title": "t"})

    async def change(db, action):
        async with AsyncSession(db, expire_on_commit=False) as session:
            admin = await session.scalar(select(User).where(User.username == "admin1"))
            try:
                await action(session, admin)
                return "ok"
            except RecordNotFoundError:
                return "missing"

    async def update(session, admin):
        await update_record(session, admin, record_id, RecordUpdate(title="new"))

    async def remove(session, admin):
        await delete_record(session, admin, record_id)

    async def race(actions):
        db = engine()
        try:
            return await asyncio.gather(*(change(db, action) for action in actions))
        finally:
            await db.dispose()

    assert asyncio.run(race([update] * 5)) == ["ok"] * 5
    results = asyncio.run(race([update, remove, update, update]))
    assert results[1] == "ok"
    assert set(results) <= {"ok", "missing"}
    assert count_records() == 0


def test_create_succeeds_when_the_creator_row_changed_after_authentication(client):
    """An insert checks the foreign key against a users row changed since this snapshot."""
    from app.records.schemas import RecordCreate
    from app.records.service import create_record

    async def scenario():
        db = engine()
        try:
            async with AsyncSession(db, expire_on_commit=False) as first, AsyncSession(db) as second:
                user = await first.scalar(select(User).where(User.username == "user1"))
                other = await second.scalar(select(User).where(User.username == "user1"))
                other.current_session_id = "changed"
                await second.commit()
                return await create_record(first, user, RecordCreate(title="t", value=1, category="c"))
        finally:
            await db.dispose()

    assert asyncio.run(scenario()).creator_name == "user1"


# --- Audit (PRD-14-R6, PRD-05-R9) ---


def audit_records(caplog):
    return [record for record in caplog.records if record.name == "app.audit"]


def test_changes_are_logged_without_content(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.audit"):
        caplog.clear()
        record = create(client, title="secret-old-title", value=12345.5)
        call(client, "PATCH", f"{RECORDS}/{record['id']}", "user1", json={"title": "secret-new-title", "value": 54321.5})
        call(client, "DELETE", f"{RECORDS}/{record['id']}", "user1")

    events = {entry.event_type: entry for entry in audit_records(caplog) if entry.event_type.startswith("record_")}
    assert set(events) == {"record_created", "record_updated", "record_deleted"}
    for entry in events.values():
        assert entry.record_id == record["id"]
        assert entry.user_id == client.ids["user1"]
    assert events["record_updated"].fields == ["title", "value"]
    for entry in audit_records(caplog):
        text = str(entry.__dict__)
        assert "secret-" not in text and "12345.5" not in text and "54321.5" not in text


def test_failed_requests_log_no_change_and_denials_are_logged(client, caplog):
    record = create(client, "user1")
    with caplog.at_level(logging.INFO, logger="app.audit"):
        caplog.clear()
        call(client, "POST", RECORDS, "user1", json={"title": ""})
        call(client, "DELETE", f"{RECORDS}/{record['id']}", "user2")
        call(client, "POST", RECORDS, "viewer1", json=VALID)
    events = [entry.event_type for entry in audit_records(caplog)]
    assert not [event for event in events if event.startswith("record_")]
    assert events.count("permission_denied") == 2
    denied = [entry for entry in audit_records(caplog) if getattr(entry, "reason", None) == "not_record_owner"]
    assert denied[0].record_id == record["id"]


# --- Categories ---


def test_categories_are_the_values_that_actually_occur(client):
    assert call(client, "GET", CATEGORIES, "viewer1").json() == {"items": []}
    for category in ("溫度", "壓力", "溫度", "Temp", "temp", "Ａ", "A"):
        create(client, category=category)
    [system_id] = seed({"category": "振動"})
    items = call(client, "GET", CATEGORIES, "viewer1").json()["items"]
    assert sorted(items) == sorted(["溫度", "壓力", "Temp", "temp", "Ａ", "A", "振動"])
    assert call(client, "DELETE", f"{RECORDS}/{system_id}", "admin1").status_code == 204
    assert "振動" not in call(client, "GET", CATEGORIES, "viewer1").json()["items"]


# --- Filters ---


def test_empty_results_are_not_errors(client):
    create(client)
    for params in ({"title": "no-such"}, {"category": "no-such"}, {"value_min": 1e6}):
        response = call(client, "GET", RECORDS, "viewer1", params=params)
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "limit": 20, "next_cursor": None}


def test_filters_alone_and_combined_with_paging(client):
    t = BASE_TIME
    ids = seed(
        {"title": "Alpha 100% done", "value": 1.0, "category": "溫度", "timestamp": t},
        {"title": "alpha_two", "value": 2.0, "category": "溫度", "timestamp": t + timedelta(hours=1)},
        {"title": "ÁLPHA three", "value": 3.0, "category": "壓力", "timestamp": t + timedelta(hours=2)},
        {"title": "beta", "value": 4.0, "category": "壓力", "timestamp": t + timedelta(hours=3)},
        {"title": "beta 100x", "value": 5.0, "category": "temp", "timestamp": t + timedelta(hours=4)},
        {"title": "alphaXtwo", "value": 6.0, "category": "Temp", "timestamp": t + timedelta(hours=5)},
    )
    a, b, c, d, e, f = ids

    def found(**params):
        return set(ids_of(walk(client, limit=2, **params)))

    assert found(title="alpha") == {a, b, c, f}
    assert found(title="100%") == {a}
    assert found(title="alpha_") == {b}
    assert found(title="   ") == set(ids)
    assert found(category="溫度") == {a, b}
    assert found(category="temp") == {e}
    assert found(category="Temp") == {f}
    assert found(category=" ") == set(ids)
    assert found(value_min=2) == {b, c, d, e, f}
    assert found(value_max=2) == {a, b}
    assert found(value_min=2, value_max=3) == {b, c}
    assert found(time_from="2026-01-01T01:00:00Z") == {b, c, d, e, f}
    assert found(time_to="2026-01-01T01:00:00Z") == {a, b}
    assert found(time_from="2026-01-01T09:00:00+08:00", time_to="2026-01-01T10:00:00+08:00") == {b, c}
    assert found(title="alpha", category="溫度", value_min=2, time_to="2026-01-01T02:00:00Z") == {b}

    assert ids_of(walk(client, limit=1, category="壓力", sort_by="value", order="asc")) == [c, d]
    assert ids_of(walk(client, limit=1, category="壓力", sort_by="value", order="desc")) == [d, c]


@pytest.mark.parametrize(
    "params, field",
    [
        ({"value_min": 2, "value_max": 1}, "query.value_max"),
        ({"time_from": "2026-01-02T00:00:00Z", "time_to": "2026-01-01T00:00:00Z"}, "query.time_to"),
        ({"value_min": "abc"}, "query.value_min"),
        ({"value_min": "nan"}, "query.value_min"),
        ({"value_max": "inf"}, "query.value_max"),
        ({"time_from": "2026-01-01T00:00:00"}, "query.time_from"),
        ({"time_to": "not-a-time"}, "query.time_to"),
        ({"limit": 0}, "query.limit"),
        ({"limit": 101}, "query.limit"),
        ({"sort_by": "id"}, "query.sort_by"),
        ({"sort_by": "creator"}, "query.sort_by"),
        ({"order": "sideways"}, "query.order"),
        ({"title": "x" * 256}, "query.title"),
        ({"category": "x" * 101}, "query.category"),
    ],
)
def test_bad_list_parameters_name_the_field(client, params, field):
    assert field in error_fields(call(client, "GET", RECORDS, "viewer1", params=params))


def test_equal_range_bounds_are_allowed(client):
    [record_id] = seed({"value": 5.0})
    assert ids_of(walk(client, value_min=5, value_max=5)) == [record_id]


def test_limit_boundaries(client):
    seed(*[{"title": f"t{i}"} for i in range(101)])
    assert len(call(client, "GET", RECORDS, "viewer1").json()["items"]) == 20
    assert len(call(client, "GET", RECORDS, "viewer1", params={"limit": 1}).json()["items"]) == 1
    full = call(client, "GET", RECORDS, "viewer1", params={"limit": 100}).json()
    assert len(full["items"]) == 100 and full["total"] == 101 and full["next_cursor"]


# --- Pagination ---

SORT_RUNS = list(product(("asc", "desc"), (1, 2, 3)))


def assert_complete_and_ordered(rows, expected_ids, key, order):
    got = ids_of(rows)
    assert len(got) == len(set(got)), "duplicate rows across pages"
    assert set(got) == set(expected_ids), "rows dropped across pages"
    sign = 1 if order == "asc" else -1
    keys = [(sign * key(row), sign * row["id"]) for row in rows]
    assert keys == sorted(keys)


def test_every_sort_with_duplicate_values_has_no_duplicates_or_drops(client):
    ids = seed(
        *[
            {
                "title": f"title-{i % 3}",
                "value": float(i % 4),
                "category": f"cat-{i % 2}",
                "timestamp": BASE_TIME + timedelta(minutes=i % 5),
            }
            for i in range(30)
        ]
    )
    for sort_by, (order, limit) in product(("title", "value", "category", "timestamp"), SORT_RUNS):
        rows = walk(client, limit=limit, sort_by=sort_by, order=order)
        got = ids_of(rows)
        assert len(got) == len(set(got)) and set(got) == set(ids), (sort_by, order, limit)
        # Ties come in id order, following the sort direction.
        tied = [row["id"] for row in rows if row[sort_by] == rows[0][sort_by]]
        assert tied == sorted(tied, reverse=order == "desc")


DOUBLE_KEYS = [
    0.1,
    0.1 + 0.2,
    279.3,
    1e-300,
    -1e-300,
    1.7e308,
    -1.7e308,
    1 / 3,
    -0.0,
    0.0,
    5e-324,
    2.2250738585072014e-308,
    -1.2345678901234567e-300,
    1.7976931348623157e308,
    -1.7976931348623157e308,
    123456789012345678.0,
]


@pytest.mark.parametrize("order, limit", SORT_RUNS)
def test_seek_survives_double_keys(client, order, limit):
    # Every key three times, and -0.0 and 0.0 interleaved, so that page edges land inside ties.
    ids = seed(*[{"value": value} for value in DOUBLE_KEYS * 3], *[{"value": 42.5} for _ in range(25)])
    rows = walk(client, limit=limit, sort_by="value", order=order)
    assert_complete_and_ordered(rows, ids, lambda row: row["value"], order)
    assert sorted(ids_of(rows)) == sorted(ids)
    returned = {row["id"]: row["value"] for row in rows}
    stored = dict(zip(ids, DOUBLE_KEYS * 3 + [42.5] * 25))
    assert returned == stored


def test_negative_zero_and_zero_are_one_tie_in_id_order(client):
    ids = seed({"value": 0.0}, {"value": -0.0}, {"value": 0.0}, {"value": -0.0})
    assert ids_of(walk(client, limit=1, sort_by="value", order="asc")) == ids
    assert ids_of(walk(client, limit=1, sort_by="value", order="desc")) == ids[::-1]
    # Filtering on either zero finds them all, as sorting says they are equal.
    assert set(ids_of(walk(client, value_min=-0.0, value_max=0.0))) == set(ids)


@pytest.mark.parametrize("order, limit", SORT_RUNS)
def test_seek_survives_titles_equal_under_the_collation(client, order, limit):
    titles = ["Cafe", "café", "CAFE", "cafe", "CAFÉ", "Café"] * 3
    ids = seed(*[{"title": title} for title in titles], {"title": "Bakery"}, {"title": "Deli"})
    rows = walk(client, limit=limit, sort_by="title", order=order)
    got = ids_of(rows)
    assert len(got) == len(set(got)) and set(got) == set(ids)
    # The tie group is contiguous, kept byte for byte, and in id order.
    middle = rows[1:-1]
    assert [row["id"] for row in middle] == sorted(ids[: len(titles)], reverse=order == "desc")
    assert [row["title"] for row in middle] == (titles if order == "asc" else titles[::-1])
    assert (rows[0]["title"], rows[-1]["title"]) == (("Bakery", "Deli") if order == "asc" else ("Deli", "Bakery"))


@pytest.mark.parametrize("order, limit", SORT_RUNS)
def test_seek_survives_timestamps_apart_by_microseconds(client, order, limit):
    ids = seed(
        *[{"timestamp": BASE_TIME + timedelta(microseconds=i % 7)} for i in range(21)],
        {"timestamp": datetime(2026, 1, 1, 0, 0, 0, 999999)},
        {"timestamp": datetime(2026, 1, 1, 0, 0, 1)},
    )
    rows = walk(client, limit=limit, sort_by="timestamp", order=order)
    assert_complete_and_ordered(rows, ids, lambda row: utc(row["timestamp"]).timestamp(), order)
    keys = [utc(row["timestamp"]) for row in rows]
    assert keys == sorted(keys, reverse=order == "desc")
    assert {utc(row["timestamp"]).microsecond for row in rows} >= set(range(7)) | {999999}


def test_seek_survives_category_spellings(client):
    categories = ["Temp", "temp", "TEMP", "Ａ", "A", "溫度"] * 2
    ids = seed(*[{"category": category} for category in categories])
    for order, limit in SORT_RUNS:
        got = ids_of(walk(client, limit=limit, sort_by="category", order=order))
        assert len(got) == len(set(got)) and set(got) == set(ids)


def test_inserts_between_pages_do_not_duplicate_or_drop(client):
    ids = seed(*[{"timestamp": BASE_TIME + timedelta(seconds=i)} for i in range(10)])
    first = call(client, "GET", RECORDS, "viewer1", params={"limit": 3}).json()
    seen = ids_of(first["items"])
    cursor = first["next_cursor"]
    while cursor:
        # New rows land on top, as the simulator's do, which would push offsets along.
        seed({"timestamp": BASE_TIME + timedelta(days=1)})
        page = call(client, "GET", RECORDS, "viewer1", params={"limit": 3, "cursor": cursor}).json()
        seen.extend(ids_of(page["items"]))
        cursor = page["next_cursor"]
    assert seen == ids[::-1]


def test_deletes_between_pages_do_not_drop_later_rows(client):
    ids = seed(*[{"timestamp": BASE_TIME + timedelta(seconds=i)} for i in range(10)])
    newest_first = ids[::-1]
    first = call(client, "GET", RECORDS, "viewer1", params={"limit": 3}).json()
    # Removing rows already shown, including the one the cursor points at.
    assert call(client, "DELETE", f"{RECORDS}/{newest_first[0]}", "admin1").status_code == 204
    assert call(client, "DELETE", f"{RECORDS}/{newest_first[2]}", "admin1").status_code == 204
    second = call(client, "GET", RECORDS, "viewer1", params={"limit": 3, "cursor": first["next_cursor"]}).json()
    assert ids_of(second["items"]) == newest_first[3:6]
    assert second["total"] == 8


def test_page_emptied_by_deletion_is_200_with_real_total(client):
    ids = seed(*[{"timestamp": BASE_TIME + timedelta(seconds=i)} for i in range(4)])
    newest_first = ids[::-1]
    first = call(client, "GET", RECORDS, "viewer1", params={"limit": 2}).json()
    assert first["next_cursor"] and first["total"] == 4
    for record_id in newest_first[2:]:
        assert call(client, "DELETE", f"{RECORDS}/{record_id}", "admin1").status_code == 204

    response = call(client, "GET", RECORDS, "viewer1", params={"limit": 2, "cursor": first["next_cursor"]})
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 2, "limit": 2, "next_cursor": None}


@pytest.mark.parametrize(
    "cursor", ["garbage", "!!!", "e30", "eyJzIjoidmFsdWUifQ", "W10", "bnVsbA", "eyJzIjoidmFsdWUiLCJvIjoiZGVzYyIsImsiOiJ4IiwiaSI6MX0"]
)
def test_tampered_cursor_is_rejected(client, cursor):
    seed({"value": 1.0})
    params = {"cursor": cursor, "sort_by": "value"}
    assert "query.cursor" in error_fields(call(client, "GET", RECORDS, "viewer1", params=params))


def test_cursor_from_another_sort_is_rejected(client):
    seed(*[{"title": f"t{i}"} for i in range(3)])
    cursor = call(client, "GET", RECORDS, "viewer1", params={"limit": 1}).json()["next_cursor"]
    for params in ({"sort_by": "value"}, {"order": "asc"}, {"sort_by": "title"}):
        response = call(client, "GET", RECORDS, "viewer1", params={"limit": 1, "cursor": cursor, **params})
        assert "query.cursor" in error_fields(response)


# --- Schema ---


def test_records_keep_their_creator_from_being_removed(client):
    """A user with records cannot vanish and turn them into system records."""
    from sqlalchemy.exc import IntegrityError

    record = create(client, "user2")

    async def remove(session):
        await session.execute(delete(User).where(User.id == record["creator_id"]))
        await session.commit()

    with pytest.raises(IntegrityError):
        run_in_db(remove)


# --- Retrying a create: client_request_id ---


def retry_body(request_id=None, **fields):
    return {**VALID, "client_request_id": request_id or str(uuid4()), **fields}


def test_retry_with_same_key_returns_the_saved_record_without_adding_one(client):
    body = retry_body()
    first = call(client, "POST", RECORDS, "user1", json=body)
    second = call(client, "POST", RECORDS, "user1", json=body)
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert "client_request_id" not in first.json()
    assert count_records() == 1


def test_without_a_key_identical_creates_stay_separate_records(client):
    create(client, "user1")
    create(client, "user1")
    assert count_records() == 2


def test_new_key_with_identical_content_is_a_new_record(client):
    call(client, "POST", RECORDS, "user1", json=retry_body())
    call(client, "POST", RECORDS, "user1", json=retry_body())
    assert count_records() == 2


def test_same_key_with_other_content_is_refused(client):
    request_id = str(uuid4())
    call(client, "POST", RECORDS, "user1", json=retry_body(request_id))
    response = call(client, "POST", RECORDS, "user1", json=retry_body(request_id, value=1.5))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLIENT_REQUEST_REUSED"
    assert count_records() == 1


def test_same_key_with_other_timestamp_is_refused_but_omitted_timestamp_is_a_retry(client):
    request_id = str(uuid4())
    body = retry_body(request_id, timestamp="2026-03-01T08:00:00+08:00")
    assert call(client, "POST", RECORDS, "user1", json=body).status_code == 201
    assert call(client, "POST", RECORDS, "user1", json=body).status_code == 201
    other = {**body, "timestamp": "2026-03-01T09:00:00+08:00"}
    assert call(client, "POST", RECORDS, "user1", json=other).status_code == 409
    assert call(client, "POST", RECORDS, "user1", json=retry_body(request_id)).status_code == 201
    assert count_records() == 1


def test_key_belongs_to_one_user(client):
    request_id = str(uuid4())
    first = call(client, "POST", RECORDS, "user1", json=retry_body(request_id))
    second = call(client, "POST", RECORDS, "user2", json=retry_body(request_id))
    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["creator_name"] == "user2"


def test_invalid_key_names_the_field(client):
    response = call(client, "POST", RECORDS, "user1", json=retry_body("not-a-uuid"))
    assert response.status_code == 422
    assert [problem["field"] for problem in response.json()["error"]["details"]] == ["body.client_request_id"]
    assert count_records() == 0


def test_retry_of_a_saved_anomaly_gets_it_without_a_new_judgement(client):
    category = f"retry-{uuid4().hex[:8]}"

    async def set_range(session, low, high):
        setting = await session.get(CategorySetting, category)
        if setting is None:
            setting = CategorySetting(category=category)
            session.add(setting)
        setting.range_min, setting.range_max = low, high
        await session.commit()

    run_in_db(lambda session: set_range(session, 0.0, 10.0))
    body = retry_body(category=category, value=50.0, confirm_anomaly=True)
    first = call(client, "POST", RECORDS, "user1", json=body)
    assert first.json()["is_anomaly"] is True

    # The range moved, and this retry has no confirmation: it must still get the saved record.
    run_in_db(lambda session: set_range(session, 0.0, 20.0))
    second = call(client, "POST", RECORDS, "user1", json={**body, "confirm_anomaly": False})
    assert second.status_code == 201
    assert second.json() == first.json()
    assert count_records() == 1

    async def clean(session):
        await session.execute(delete(CategorySetting).where(CategorySetting.category == category))
        await session.commit()

    run_in_db(clean)


def test_concurrent_sends_of_one_request_add_one_record(client):
    from app.records.schemas import RecordCreate
    from app.records.service import create_record

    payload = RecordCreate.model_validate(retry_body())

    async def send(db):
        async with AsyncSession(db, expire_on_commit=False) as session:
            user = await session.scalar(select(User).where(User.username == "user1"))
            return (await create_record(session, user, payload)).id

    async def race():
        db = engine()
        try:
            return await asyncio.gather(*(send(db) for _ in range(6)))
        finally:
            await db.dispose()

    ids = asyncio.run(race())
    assert len(set(ids)) == 1
    assert count_records() == 1


def test_records_without_a_key_may_share_a_creator_at_the_database(client):
    seed({"title": "a"}, {"title": "b"}, creator_id=client.ids["user1"])
    seed({"title": "system a"}, {"title": "system b"})
    assert count_records() == 4
