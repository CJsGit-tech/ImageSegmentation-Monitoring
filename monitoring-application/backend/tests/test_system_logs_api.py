"""System log query (PRD-14) against a real MariaDB, with the real lifespan: events go through
the audit handler and the batch writer. Run through tests/run_integration.sh."""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.service import register_user
from app.core.config import load_settings
from app.db.migrate import upgrade_to_head
from app.db.session import create_unpooled_engine, get_engine, get_session_factory
from app.main import create_app
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.record_import import RecordImport
from app.models.system_log import Severity, SystemLog
from app.models.user import Role, User
from app.prestart import _wait_for_database
from app.syslog.retention import purge_expired

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

GOOD_PASSWORD = "Str0ng!pass"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
LOGS = "/api/v1/logs"
OPTIONS = "/api/v1/logs/options"
USERS = "/api/v1/users"
RECORDS = "/api/v1/records"
IMPORTS = "/api/v1/records/imports"
SETTINGS = "/api/v1/category-settings"
INTERVAL = 0.2
ACCOUNTS = {"logadmin": Role.ADMIN, "loguser": Role.USER, "logviewer": Role.VIEWER, "logtarget": Role.VIEWER}


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


async def _clear_all(session):
    await session.execute(delete(SystemLog))
    await session.execute(delete(Record))
    await session.execute(delete(RecordImport))
    await session.execute(delete(CategorySetting))
    await session.execute(delete(User))
    await session.commit()


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


def iso(value):
    return value.replace(tzinfo=UTC).isoformat()


@pytest.fixture(scope="module")
def live():
    os.environ.update(
        DB_HOST=os.environ["TEST_DB_HOST"],
        DB_PORT=os.environ.get("TEST_DB_PORT", "3306"),
        DB_NAME=os.environ.get("TEST_DB_NAME", "monitoring_test"),
        DB_USER=os.environ.get("TEST_DB_USER", "monitoring"),
        DB_PASSWORD=os.environ["TEST_DB_PASSWORD"],
        SIMULATOR_INTERVAL_SECONDS=str(INTERVAL),
    )
    Env.settings = load_settings()

    async def prepare():
        db = create_unpooled_engine(Env.settings)
        try:
            await _wait_for_database(db, 90)
            await upgrade_to_head(db)
            async with AsyncSession(db, expire_on_commit=False) as session:
                await _clear_all(session)
                for username, role in ACCOUNTS.items():
                    user = await register_user(session, username, GOOD_PASSWORD)
                    user.role = role
                await session.commit()
        finally:
            await db.dispose()

    asyncio.run(prepare())
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    app = create_app()

    async def boom():
        raise RuntimeError("must-not-be-stored")

    app.add_api_route("/boom", boom)
    with TestClient(app, raise_server_exceptions=False) as client:

        class Live:
            http = client
            application = app
            headers: dict[str, dict[str, str]] = {}

        for username in ACCOUNTS:
            login(Live, username)
        yield Live
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    run_in_db(_clear_all)
    os.environ.pop("SIMULATOR_INTERVAL_SECONDS", None)


def login(live, username, password=GOOD_PASSWORD):
    response = live.http.post(LOGIN, json={"username": username, "password": password})
    if response.status_code == 200:
        live.headers[username] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return response


def call(live, method, path, who=None, **kwargs):
    return live.http.request(method, path, headers=live.headers[who] if who else {}, **kwargs)


def logs(live, who="logadmin", **params):
    now = utc_now()
    params.setdefault("start", iso(now - timedelta(hours=1)))
    params.setdefault("end", iso(now + timedelta(hours=1)))
    return call(live, "GET", LOGS, who, params=params)


def all_items(live, **params):
    items, cursor = [], None
    while True:
        response = logs(live, **params, **({"cursor": cursor} if cursor else {}), limit=100)
        assert response.status_code == 200, response.text
        body = response.json()
        items += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items


def wait_for(live, predicate, timeout=10.0, **params):
    deadline = time.monotonic() + timeout
    while True:
        items = all_items(live, **params)
        if predicate(items) or time.monotonic() > deadline:
            return items
        time.sleep(0.3)


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    return error


def insert_rows(rows):
    async def work(session):
        session.add_all(rows)
        await session.commit()
        return [row.id for row in rows]

    return run_in_db(work)


def log_row(occurred_at, event_type="logout", severity=Severity.INFO, username="someone"):
    return SystemLog(
        occurred_at=occurred_at,
        event_type=event_type,
        event_name=event_type,
        severity=severity,
        user_id=None,
        username=username,
        summary="test",
        details=None,
    )


# --- Every R6 event reaches the log ---


def test_every_r6_event_appears_in_the_log(live):
    started = utc_now() - timedelta(seconds=1)
    # Login failures, a superseded login and a logout.
    assert login(live, "nobody-here", "Wr0ng!pass").status_code == 401
    assert login(live, "loguser", "Wr0ng!pass").status_code == 401
    assert login(live, "loguser").status_code == 200
    assert call(live, "POST", LOGOUT, "loguser").status_code == 204
    assert login(live, "loguser").status_code == 200
    # Permission denied.
    assert call(live, "GET", LOGS, "logviewer").status_code == 403
    # Role change.
    target = run_in_db(lambda s: s.scalar(select(User.id).where(User.username == "logtarget")))
    assert call(live, "PATCH", f"{USERS}/{target}/role", "logadmin", json={"role": "User"}).status_code == 200
    # Record create, update, delete.
    body = {"title": "log-secret-title", "value": 1.5, "category": "syslog-cat"}
    created = call(live, "POST", RECORDS, "loguser", json=body)
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]
    updated = call(live, "PATCH", f"{RECORDS}/{record_id}", "loguser", json={"title": "log-secret-2"})
    assert updated.status_code == 200, updated.text
    assert call(live, "DELETE", f"{RECORDS}/{record_id}", "loguser").status_code == 204
    # Import accepted and refused.
    csv = "title,value,category,timestamp\nimp,1,syslog-imp,2026-01-01T00:00:00+00:00\n".encode()
    files = {"file": ("syslog-readings.csv", csv, "application/octet-stream")}
    assert call(live, "POST", IMPORTS, "loguser", files=files).status_code == 201
    bad = {"file": ("syslog.txt", b"x", "application/octet-stream")}
    assert call(live, "POST", IMPORTS, "loguser", files=bad).status_code in (400, 415, 422)
    # Range and unit set, changed and cleared.
    for change in (
        {"range": {"min": 0, "max": 10}, "unit": "°C"},
        {"range": {"min": 1, "max": 9}, "unit": "K"},
        {"range": None, "unit": None},
    ):
        response = call(live, "PATCH", SETTINGS, "logadmin", json={"category": "syslog-range", **change})
        assert response.status_code == 200, response.text
    # A system error.
    assert live.http.get("/boom").status_code == 500

    expected = {
        "login_failure": "login_failure",
        "login_success": "login_success",
        "logout": "logout",
        "login_superseded": "login_superseded",
        "permission_denied": "permission_denied",
        "user_role_changed": "role_changed",
        "record_created": "record_created",
        "record_updated": "record_updated",
        "record_deleted": "record_deleted",
        "records_imported": "import_result",
        "records_import_rejected": "import_result",
        "category_range_set": "range_unit_changed",
        "category_range_changed": "range_unit_changed",
        "category_range_cleared": "range_unit_changed",
        "category_unit_set": "range_unit_changed",
        "category_unit_changed": "range_unit_changed",
        "category_unit_cleared": "range_unit_changed",
        "system_error": "system_error",
    }
    items = wait_for(
        live, lambda found: expected.keys() <= {item["event_name"] for item in found}, start=iso(started)
    )
    names = {item["event_name"]: item for item in items}
    assert expected.keys() <= names.keys(), sorted(expected.keys() - names.keys())
    for name, group in expected.items():
        item = names[name]
        assert item["event_type"] == group and item["event_label"] and item["summary"]
    assert names["login_failure"]["severity"] == "warning"
    assert names["permission_denied"]["username"] == "logviewer"
    assert names["user_role_changed"]["details"]["target_username"] == "logtarget"
    assert names["records_imported"]["details"]["row_count"] == 1
    assert names["system_error"]["severity"] == "error" and names["system_error"]["user_id"] is None
    unknown = [i for i in items if i["event_name"] == "login_failure" and i["username"] is None]
    assert unknown and unknown[0]["summary"] == "登入失敗：帳號不存在"
    text = repr(items)
    for secret in ("Wr0ng!pass", GOOD_PASSWORD, "log-secret", "must-not-be-stored", "nobody-here", "Bearer"):
        assert secret not in text


def test_unauthenticated_requests_are_not_logged_as_denials(live):
    started = utc_now()
    assert call(live, "GET", LOGS).status_code == 401
    time.sleep(1.5)
    items = all_items(live, start=iso(started), event_type="permission_denied")
    assert items == []


def test_simulated_readings_never_reach_the_log(live):
    simulator = live.application.state.realtime_runtime.simulator
    written_before = simulator.written_count
    started = utc_now()
    time.sleep(2.5)
    assert simulator.written_count > written_before
    items = all_items(live, start=iso(started))
    assert [i for i in items if i["event_type"] != "system_error"] == []
    system_rows = run_in_db(
        lambda s: s.scalar(select(func.count()).select_from(Record).where(Record.created_by_user_id.is_(None)))
    )
    assert system_rows > 0


# --- Query contract ---


@pytest.fixture
def window(live):
    """45 rows 20 days ago, each minute, with some ties, away from live events."""
    base = (utc_now() - timedelta(days=20)).replace(microsecond=0)
    rows = []
    for i in range(45):
        rows.append(
            log_row(
                base + timedelta(minutes=i // 3 * 3),  # three rows share each time
                event_type=("logout", "login_failure", "record_created")[i % 3],
                severity=(Severity.INFO, Severity.WARNING, Severity.INFO)[i % 3],
                username=("alice", "Bob", "alice")[i % 3] if i % 5 else None,
            )
        )
    ids = insert_rows(rows)
    yield base, dict(zip(ids, rows))
    run_in_db(lambda s: _delete(s, ids))


async def _delete(session, ids):
    await session.execute(delete(SystemLog).where(SystemLog.id.in_(ids)))
    await session.commit()


def window_params(base):
    return {"start": iso(base - timedelta(minutes=1)), "end": iso(base + timedelta(hours=1))}


def test_pages_are_newest_first_without_gaps_or_repeats(live, window):
    base, rows = window
    seen, cursor, totals = [], None, set()
    while True:
        params = {**window_params(base), "limit": 7, **({"cursor": cursor} if cursor else {})}
        response = call(live, "GET", LOGS, "logadmin", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= 7
        totals.add(body["total"])
        seen += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert totals == {45}
    assert sorted(item["id"] for item in seen) == sorted(rows)
    keys = [(item["occurred_at"], item["id"]) for item in seen]
    assert keys == sorted(keys, reverse=True)
    assert len(set(keys)) == 45


def test_filters_combine(live, window):
    base, rows = window
    params = {**window_params(base), "event_type": ["login_failure", "record_created"], "severity": "warning",
              "username": "bob"}
    items = all_items(live, **params)
    expected = {
        row_id for row_id, row in rows.items()
        if row.event_type == "login_failure" and row.severity is Severity.WARNING and row.username == "Bob"
    }
    assert expected and {item["id"] for item in items} == expected
    assert all(item["username"] == "Bob" for item in items)
    # Each filter alone narrows too.
    assert len(all_items(live, **window_params(base), event_type="logout")) == 15
    assert len(all_items(live, **window_params(base), severity="warning")) == 15
    assert len(all_items(live, **window_params(base), username="ALICE")) == sum(
        1 for row in rows.values() if row.username == "alice"
    )
    # Blank user means no user filter; it is not a keyword search.
    assert len(all_items(live, **window_params(base), username="  ")) == 45
    assert all_items(live, **window_params(base), username="ali") == []


def test_no_match_is_an_empty_page(live, window):
    base, _ = window
    response = call(live, "GET", LOGS, "logadmin", params={**window_params(base), "event_type": "system_error"})
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "next_cursor": None}


def test_a_cursor_from_other_conditions_is_refused(live, window):
    base, _ = window
    first = call(live, "GET", LOGS, "logadmin", params={**window_params(base), "limit": 5}).json()
    response = call(
        live, "GET", LOGS, "logadmin",
        params={**window_params(base), "limit": 5, "severity": "info", "cursor": first["next_cursor"]},
    )
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"][0]["type"] == "cursor_invalid"
    garbage = call(live, "GET", LOGS, "logadmin", params={**window_params(base), "cursor": "!!"})
    assert_error(garbage, 422, "VALIDATION_ERROR")


def test_three_months_exactly_is_allowed_and_one_second_more_is_refused(live):
    start = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)
    exact = call(live, "GET", LOGS, "logadmin", params={"start": start.isoformat(), "end": "2026-11-08T00:00:00+00:00"})
    assert exact.status_code == 200, exact.text
    over = call(live, "GET", LOGS, "logadmin", params={"start": start.isoformat(), "end": "2026-11-08T00:00:01+00:00"})
    error = assert_error(over, 422, "RANGE_TOO_LONG")
    assert error["details"]["max_months"] == 3
    reversed_ = call(live, "GET", LOGS, "logadmin", params={"start": start.isoformat(), "end": start.isoformat()})
    assert_error(reversed_, 422, "VALIDATION_ERROR")
    for params in ({"end": start.isoformat()}, {"start": "2026-08-08T00:00:00", "end": "2026-08-09T00:00:00"}):
        assert_error(call(live, "GET", LOGS, "logadmin", params=params), 422, "VALIDATION_ERROR")
    bad_type = call(live, "GET", LOGS, "logadmin", params={"start": start.isoformat(),
                                                             "end": "2026-08-09T00:00:00+00:00", "event_type": "x"})
    assert_error(bad_type, 422, "VALIDATION_ERROR")


# --- Access and read-only ---


@pytest.mark.parametrize("who", ["loguser", "logviewer"])
@pytest.mark.parametrize("path", [LOGS, OPTIONS])
def test_only_admin_may_read_the_log(live, who, path):
    response = call(live, "GET", path, who, params={"start": iso(utc_now() - timedelta(hours=1)), "end": iso(utc_now())})
    assert_error(response, 403, "FORBIDDEN")
    assert_error(call(live, "GET", path), 401, "UNAUTHORIZED")


def test_options_list_the_screen_choices(live):
    body = call(live, "GET", OPTIONS, "logadmin").json()
    assert [o["value"] for o in body["event_types"]] == [
        "login_success", "login_failure", "logout", "login_superseded", "permission_denied", "role_changed",
        "record_created", "record_updated", "record_deleted", "import_result", "range_unit_changed", "system_error",
    ]
    assert body["severities"] == [
        {"value": "info", "label": "資訊"}, {"value": "warning", "label": "警告"}, {"value": "error", "label": "錯誤"}
    ]
    assert body["retention_days"] == 365 and body["max_range_months"] == 3


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_log_cannot_be_changed_through_the_api(live, method):
    assert call(live, method, LOGS, "logadmin").status_code == 405
    paths = live.http.get("/openapi.json").json()["paths"]
    for path, operations in paths.items():
        if path.startswith(LOGS):
            assert set(operations) == {"get"}


# --- Retention ---


def test_cleanup_removes_rows_past_retention_only(live):
    now = utc_now()
    ids = insert_rows([
        log_row(now - timedelta(days=366), username="old"),
        log_row(now - timedelta(days=364), username="kept"),
        log_row(now - timedelta(hours=13), username="recent"),
    ])

    async def purge(days):
        db = create_unpooled_engine(Env.settings)
        try:
            return await purge_expired(async_sessionmaker(db, expire_on_commit=False), days, now=now)
        finally:
            await db.dispose()

    assert asyncio.run(purge(365)) >= 1
    remaining = run_in_db(lambda s: s.scalars(select(SystemLog.username).where(SystemLog.id.in_(ids))))
    assert sorted(remaining) == ["kept", "recent"]
    # A shorter setting removes more.
    asyncio.run(purge(0.5))
    remaining = run_in_db(lambda s: s.scalars(select(SystemLog.username).where(SystemLog.id.in_(ids))))
    assert list(remaining) == []
