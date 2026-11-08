"""Simulator, live window, WebSocket push and custom ranges (PRD-07, PRD-08) against MariaDB.

The application runs with its lifespan, so the real simulator generates every 0.2 s throughout
this module. Run through tests/run_integration.sh.
"""

import asyncio
import logging
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from app.api.v1 import realtime as realtime_api
from app.auth.service import register_user
from app.core.config import load_settings
from app.db.migrate import upgrade_to_head
from app.db.session import create_unpooled_engine, get_engine, get_session_factory
from app.main import create_app
from app.models.category_setting import CategorySetting, SettingOrigin
from app.models.record import Record
from app.models.record_import import RecordImport
from app.models.user import Role, User
from app.prestart import _prepare_database, _wait_for_database
from app.realtime.hub import RealtimeHub
from app.realtime.runtime import start_realtime
from app.records.anomaly import CategoryRange, is_out_of_range
from app.records.service import build_record
from app.simulator.devices import DEVICES
from app.simulator.service import Simulator

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

GOOD_PASSWORD = "Str0ng!pass"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
RECORDS = "/api/v1/records"
SETTINGS = "/api/v1/category-settings"
WINDOW = "/api/v1/realtime/window"
RANGE = "/api/v1/realtime/range"
WS = "/api/v1/realtime/ws"
INTERVAL = 0.2
ACCOUNTS = {"admin1": Role.ADMIN, "user1": Role.USER, "viewer1": Role.VIEWER}
CATEGORIES = [device.category for device in DEVICES]


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
    await session.execute(delete(Record))
    await session.execute(delete(RecordImport))
    await session.execute(delete(CategorySetting))
    await session.execute(delete(User))
    await session.commit()


def system_count():
    return run_in_db(
        lambda s: s.scalar(select(func.count()).select_from(Record).where(Record.created_by_user_id.is_(None)))
    )


def wait_until(predicate, timeout=5.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (result := predicate()):
            return result
        time.sleep(step)
    raise AssertionError("condition not met in time")


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
    # The real startup path: migrations, initial Admin and the device defaults.
    asyncio.run(_prepare_database(Env.settings))

    get_engine.cache_clear()
    get_session_factory.cache_clear()
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:

        class Live:
            http = client
            application = app
            headers: dict[str, dict[str, str]] = {}
            tokens: dict[str, str] = {}

        yield Live
        # The restart test replaces the runtime that the lifespan started; stop the current one too.
        client.portal.call(app.state.realtime_runtime.stop)
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    run_in_db(_clear_all)
    os.environ.pop("SIMULATOR_INTERVAL_SECONDS", None)


def login(live, username):
    response = live.http.post(LOGIN, json={"username": username, "password": GOOD_PASSWORD})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    live.tokens[username] = token
    live.headers[username] = {"Authorization": f"Bearer {token}"}
    return token


@pytest.fixture
def client(live):
    for username in ACCOUNTS:
        login(live, username)
    return live


def call(live, method, path, who=None, **kwargs):
    return live.http.request(method, path, headers=live.headers[who] if who else {}, **kwargs)


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    return error


def settings_of(live):
    items = call(live, "GET", SETTINGS, "admin1").json()["items"]
    return {item["category"]: item for item in items}


def receive_until_closed(ws, limit=200):
    """Messages until the server closes; returns (messages, WebSocketDisconnect)."""
    messages = []
    for _ in range(limit):
        try:
            messages.append(ws.receive_json())
        except WebSocketDisconnect as closed:
            return messages, closed
    raise AssertionError("socket was not closed")


def runtime(live):
    return live.application.state.realtime_runtime


# --- Defaults (PRD-07-R8, R11; PRD-17-R9) ---


def test_defaults_are_seeded_and_admin_changes_and_clears_survive_a_restart(client):
    seeded = settings_of(client)
    for device in DEVICES:
        assert seeded[device.category]["range"] == {"min": device.range_min, "max": device.range_max}
        assert seeded[device.category]["unit"] == device.unit

    def patch(**body):
        response = call(client, "PATCH", SETTINGS, "admin1", json=body)
        assert response.status_code == 200, response.text

    patch(category="溫度", range={"min": 10, "max": 90})
    patch(category="壓力", range=None)
    patch(category="振動", unit="mm/sec")
    patch(category="馬達轉速", unit=None)

    asyncio.run(_prepare_database(Env.settings))  # What every container start runs.

    after = settings_of(client)
    assert after["溫度"]["range"] == {"min": 10, "max": 90}
    assert after["壓力"]["range"] is None and after["壓力"]["range_status"] == "unset"
    assert after["壓力"]["unit"] == "kPa"
    assert after["振動"]["unit"] == "mm/sec"
    assert after["振動"]["range"] == {"min": 0.5, "max": 8}
    assert after["馬達轉速"]["unit"] is None
    origins = run_in_db(
        lambda s: s.execute(select(CategorySetting.category, CategorySetting.range_origin))
    )
    assert dict(origins.all())["馬達轉速"] == SettingOrigin.DEFAULT


# --- Generation and batch writes (PRD-07) ---


def test_generates_for_every_device_and_writes_batches_as_system(client):
    before = system_count()
    wait_until(lambda: system_count() >= before + 4 * 5)
    response = call(client, "GET", RECORDS, "viewer1", params={"limit": 40, "sort_by": "timestamp", "order": "desc"})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {item["category"] for item in items} == set(CATEGORIES)
    titles = {device.category: device.title for device in DEVICES}
    for item in items:
        assert item["creator_name"] == "系統" and item["creator_id"] is None and item["is_system"] is True
        assert item["title"] == titles[item["category"]]
        assert item["timestamp"]
    categories = call(client, "GET", f"{RECORDS}/categories", "viewer1").json()["items"]
    assert set(CATEGORIES) <= set(categories)


def test_each_device_reads_once_per_interval_with_generation_time(client):
    async def times(session):
        rows = await session.scalars(
            select(Record.timestamp).where(Record.category == "溫度", Record.created_by_user_id.is_(None))
            .order_by(Record.timestamp.desc()).limit(10)
        )
        return list(rows)

    stamps = run_in_db(times)
    gaps = sorted((a - b).total_seconds() for a, b in zip(stamps, stamps[1:]))
    assert 0.15 <= gaps[len(gaps) // 2] <= 0.4
    assert datetime.now(UTC).replace(tzinfo=None) - stamps[0] < timedelta(seconds=5)

    async def ticks(session):
        rows = await session.execute(
            select(Record.timestamp, func.count()).where(Record.created_by_user_id.is_(None))
            .group_by(Record.timestamp).order_by(Record.timestamp.desc()).limit(5)
        )
        return [count for _, count in rows]

    assert run_in_db(ticks) == [4, 4, 4, 4, 4]  # One reading per device, same generation time.


def test_pushed_readings_match_what_is_stored(client):
    with client.http.websocket_connect(WS, headers=client.headers["admin1"]) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        pushed = []
        while len(pushed) < 12:
            message = ws.receive_json()
            if message["type"] == "readings":
                assert message["simulated"] is True
                pushed.extend(message["readings"])
    keys = [
        (r["category"], datetime.fromisoformat(r["timestamp"]).astimezone(UTC).replace(tzinfo=None)) for r in pushed
    ]

    async def stored(session):
        rows = await session.execute(
            select(Record.category, Record.timestamp, Record.value, Record.is_anomaly).where(
                Record.created_by_user_id.is_(None)
            )
        )
        return {(row.category, row.timestamp): (row.value, row.is_anomaly) for row in rows}

    # Pushed first, then batch-written within a flush interval.
    def all_saved():
        rows = run_in_db(stored)
        return rows if all(key in rows for key in keys) else None

    saved = wait_until(all_saved)
    for key, reading in zip(keys, pushed):
        assert saved[key] == (reading["value"], reading["is_anomaly"])
        category_range = CategoryRange(**reading["range"]) if reading["range"] else None
        assert reading["is_anomaly"] == is_out_of_range(reading["value"], category_range)


def test_cleared_range_is_not_judged(client):
    """The defaults test cleared 壓力's range."""
    window = call(client, "GET", WINDOW, "viewer1").json()
    pressure = next(c for c in window["categories"] if c["category"] == "壓力")
    assert pressure["range"] is None and pressure["range_status"] == "unset"
    assert pressure["unit"] == "kPa"
    since = datetime.now(UTC).replace(tzinfo=None)
    time.sleep(INTERVAL * 6)

    async def flags(session):
        rows = await session.scalars(
            select(Record.is_anomaly).where(Record.category == "壓力", Record.timestamp >= since)
        )
        return list(rows)

    values = wait_until(lambda: run_in_db(flags))
    assert not any(values)


def test_admin_can_edit_and_delete_system_records_but_user_and_viewer_cannot(client):
    async def pick(session):
        return list(
            await session.scalars(
                select(Record.id).where(Record.created_by_user_id.is_(None), Record.is_anomaly.is_(False)).limit(2)
            )
        )

    first, second = run_in_db(pick)
    assert_error(call(client, "PATCH", f"{RECORDS}/{first}", "user1", json={"title": "x"}), 403, "NOT_RECORD_OWNER")
    assert_error(call(client, "DELETE", f"{RECORDS}/{first}", "user1"), 403, "NOT_RECORD_OWNER")
    assert_error(call(client, "PATCH", f"{RECORDS}/{first}", "viewer1", json={"title": "x"}), 403, "FORBIDDEN")
    assert_error(call(client, "DELETE", f"{RECORDS}/{first}", "viewer1"), 403, "FORBIDDEN")
    edited = call(client, "PATCH", f"{RECORDS}/{first}", "admin1", json={"title": "已校正"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "已校正" and edited.json()["creator_name"] == "系統"
    assert call(client, "DELETE", f"{RECORDS}/{second}", "admin1").status_code == 204


def test_generated_readings_write_no_log_entries(client, caplog):
    caplog.set_level(logging.DEBUG)
    before = runtime(client).simulator.generated_count
    time.sleep(INTERVAL * 8)
    assert runtime(client).simulator.generated_count >= before + 4 * 5
    noisy = [r for r in caplog.records if r.name in ("app.audit", "app.simulator", "app.realtime")]
    assert noisy == []


def test_a_second_simulator_does_not_generate(client):
    hub = client.application.state.realtime_hub
    assert hub.generator_running is True

    async def second():
        other_hub = RealtimeHub(get_session_factory(), interval_seconds=0.1)
        other = Simulator(other_hub, get_engine(), get_session_factory(), interval_seconds=0.1, flush_seconds=0.1)
        await other.start()
        await asyncio.sleep(0.8)
        state = (other.generated_count, other.is_leader, other_hub.generator_running)
        await other.stop()
        return state

    assert client.http.portal.call(second) == (0, False, False)
    assert hub.generator_running is True


# --- Live window over REST (PRD-08) ---


@pytest.mark.parametrize("who", list(ACCOUNTS))
def test_every_role_gets_the_live_window(client, who):
    response = call(client, "GET", WINDOW, who)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interval_seconds"] == INTERVAL
    assert body["window_seconds"] == 300
    assert body["stale_after_seconds"] == pytest.approx(2 * INTERVAL + 10)
    assert body["generator"]["running"] is True
    assert [c["category"] for c in body["categories"]] == CATEGORIES
    server_time = datetime.fromisoformat(body["server_time"])
    for category in body["categories"]:
        assert category["readings"], category["category"]
        for point in category["readings"]:
            assert server_time - datetime.fromisoformat(point["timestamp"]) <= timedelta(seconds=300)
        assert category["latest"] == category["readings"][-1]
        assert category["anomaly_count_5m"] == sum(p["is_anomaly"] for p in category["readings"])
    summary = body["summary"]
    assert summary["category_count"] == 4
    assert summary["anomaly_count_5m"] == sum(c["anomaly_count_5m"] for c in body["categories"])
    assert summary["anomalous_category_count"] == sum(c["latest"]["is_anomaly"] for c in body["categories"])
    assert summary["stalled"] is False
    assert summary["last_reading_at"] == max(c["latest"]["timestamp"] for c in body["categories"])
    current = settings_of(client)
    for category in body["categories"]:
        assert category["range"] == current[category["category"]]["range"]
        assert category["unit"] == current[category["category"]]["unit"]


def test_recent_anomalies_are_newest_first_with_range(client):
    wait_until(lambda: call(client, "GET", WINDOW, "viewer1").json()["recent_anomalies"], timeout=15)
    anomalies = call(client, "GET", WINDOW, "viewer1").json()["recent_anomalies"]
    assert len(anomalies) <= 20
    stamps = [a["timestamp"] for a in anomalies]
    assert stamps == sorted(stamps, reverse=True)
    for anomaly in anomalies:
        assert anomaly["category"] in CATEGORIES and anomaly["title"]
        assert is_out_of_range(anomaly["value"], CategoryRange(**anomaly["range"]))


@pytest.mark.parametrize("path", [WINDOW, f"{RANGE}?start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z"])
def test_unauthenticated_rest_is_refused(client, path):
    assert_error(client.http.get(path), 401, "UNAUTHORIZED")
    assert_error(client.http.get(path, headers={"Authorization": "Bearer nope"}), 401, "UNAUTHORIZED")


def test_superseded_login_is_refused_on_rest(client):
    old = dict(client.headers["viewer1"])
    login(client, "viewer1")
    assert_error(client.http.get(WINDOW, headers=old), 401, "SESSION_SUPERSEDED")


# --- WebSocket (PRD-08-R1, R5, R6) ---


@pytest.mark.parametrize("who", list(ACCOUNTS))
def test_every_role_gets_snapshot_then_readings_over_websocket(client, who):
    with client.http.websocket_connect(WS, headers=client.headers[who]) as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert [c["category"] for c in snapshot["categories"]] == CATEGORIES
        message = ws.receive_json()
        while message["type"] != "readings":
            message = ws.receive_json()
        assert [r["category"] for r in message["readings"]] == CATEGORIES
        assert set(message["category_anomaly_counts_5m"]) == set(CATEGORIES)
        assert message["summary"]["category_count"] == 4


def test_websocket_auth_by_first_message(client):
    with client.http.websocket_connect(WS) as ws:
        ws.send_json({"type": "auth", "token": client.tokens["viewer1"]})
        assert ws.receive_json()["type"] == "snapshot"


@pytest.mark.parametrize(
    "headers, first",
    [
        ({"Authorization": "Bearer nope"}, None),
        ({}, {"type": "auth", "token": "nope"}),
        ({}, {"type": "hello"}),
    ],
)
def test_websocket_refuses_bad_tokens_with_4401(client, headers, first):
    with client.http.websocket_connect(WS, headers=headers) as ws:
        if first is not None:
            ws.send_json(first)
        messages, closed = receive_until_closed(ws)
    assert messages == [{"type": "error", "error": {"code": "UNAUTHORIZED", "message": "尚未登入或登入已失效", "details": None, "request_id": None}}]
    assert closed.code == 4401 and closed.reason == "UNAUTHORIZED"


def test_websocket_without_auth_closes_after_five_seconds(client):
    started = time.monotonic()
    with client.http.websocket_connect(WS) as ws:
        messages, closed = receive_until_closed(ws)
    assert closed.code == 4408
    assert messages[0]["error"]["code"] == "AUTH_TIMEOUT"
    assert 4.5 <= time.monotonic() - started < 9


def test_newer_login_ends_the_old_connection(client):
    with client.http.websocket_connect(WS, headers=client.headers["user1"]) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        login(client, "user1")
        messages, closed = receive_until_closed(ws)
    assert closed.code == 4401 and closed.reason == "SESSION_SUPERSEDED"
    assert messages[-1]["type"] == "error" and messages[-1]["error"]["code"] == "SESSION_SUPERSEDED"


def test_logout_ends_the_connection(client):
    with client.http.websocket_connect(WS, headers=client.headers["viewer1"]) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        assert call(client, "POST", LOGOUT, "viewer1").status_code == 204
        _, closed = receive_until_closed(ws)
    assert closed.code == 4401 and closed.reason == "SESSION_LOGGED_OUT"


def test_client_messages_other_than_auth_close_with_1008(client):
    with client.http.websocket_connect(WS, headers=client.headers["admin1"]) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        ws.send_json({"type": "subscribe"})
        _, closed = receive_until_closed(ws)
    assert closed.code == 1008


def test_heartbeat_when_idle(client, monkeypatch):
    monkeypatch.setattr(realtime_api, "HEARTBEAT_SECONDS", 0.05)
    with client.http.websocket_connect(WS, headers=client.headers["admin1"]) as ws:
        types = {ws.receive_json()["type"] for _ in range(30)}
    assert "heartbeat" in types


def test_reconnect_gets_a_fresh_snapshot_and_updates_continue(client):
    with client.http.websocket_connect(WS, headers=client.headers["admin1"]) as ws:
        first = ws.receive_json()
    time.sleep(INTERVAL * 3)
    with client.http.websocket_connect(WS, headers=client.headers["admin1"]) as ws:
        second = ws.receive_json()
        assert second["type"] == "snapshot"
        assert second["summary"]["last_reading_at"] > first["summary"]["last_reading_at"]
        message = ws.receive_json()
        while message["type"] != "readings":
            message = ws.receive_json()
    assert client.application.state.realtime_hub.subscriber_count == 0


# --- Restart: shutdown flush and priming ---


def test_restart_writes_buffered_readings_and_keeps_the_window(client):
    app = client.application
    old = app.state.realtime_runtime
    client.http.portal.call(old.stop)
    assert old.simulator.buffered_count == 0
    assert old.simulator.written_count == old.simulator.generated_count
    stopped_at = datetime.now(UTC)
    saved = system_count()
    time.sleep(INTERVAL * 3)
    assert system_count() == saved  # Nothing generates while stopped.

    client.http.portal.call(start_realtime, app)
    window = call(client, "GET", WINDOW, "viewer1").json()
    stamps = [datetime.fromisoformat(p["timestamp"]) for c in window["categories"] for p in c["readings"]]
    assert any(stamp < stopped_at for stamp in stamps)  # Primed from saved records.
    wait_until(lambda: system_count() > saved)


# --- Custom range (PRD-08-R12 to R14) ---


def _seed_history(rows):
    async def work(session):
        session.add_all(rows)
        await session.commit()

    run_in_db(work)


def test_custom_range_returns_raw_points_when_few(client):
    base = datetime(2020, 1, 1, 0, 0)
    rows = []
    for device in DEVICES:
        for i in range(10):
            value = device.range_min + (device.range_max - device.range_min) / 2
            rows.append(build_record(title=device.title, value=value, category=device.category,
                                     timestamp=base + timedelta(minutes=5 * i), creator=None))
    rows.append(build_record(title="溫度感測器 T-01", value=95.0, category="溫度",
                             timestamp=base + timedelta(minutes=7), creator=None, is_anomaly=True))
    _seed_history(rows)

    response = call(client, "GET", RANGE, "viewer1",
                    params={"start": "2020-01-01T08:00:00+08:00", "end": "2020-01-01T09:00:00+08:00"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["aggregation"] == {"bucket_seconds": None, "label": "逐筆"}
    temperature = body["categories"][0]
    assert len(temperature["points"]) == 11 and temperature["anomaly_count"] == 1
    assert temperature["latest"]["timestamp"] == "2020-01-01T00:45:00Z"
    assert body["summary"] == {
        "anomalous_category_count": 1,
        "category_count": 4,
        "anomaly_count": 1,
        "last_reading_at": "2020-01-01T00:45:00Z",
    }
    assert [a["value"] for a in body["recent_anomalies"]] == [95.0]
    assert body["recent_anomalies"][0]["unit"] == "°C"


def test_custom_range_aggregates_many_points_and_keeps_anomalies(client):
    base = datetime(2020, 2, 1, 0, 0)
    rows = []
    for index, device in enumerate(DEVICES):
        for i in range(500):
            anomaly = i % 50 == 0
            value = device.range_max * 2 if anomaly else device.range_min + (device.range_max - device.range_min) / 2
            rows.append(build_record(title=device.title, value=value, category=device.category,
                                     timestamp=base + timedelta(seconds=345 * i + index), creator=None,
                                     is_anomaly=anomaly))
    _seed_history(rows)

    response = call(client, "GET", RANGE, "user1",
                    params={"start": "2020-02-01T00:00:00Z", "end": "2020-02-03T00:00:00Z"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["aggregation"] == {"bucket_seconds": 900, "label": "每 15 分鐘"}
    for category in body["categories"]:
        points = category["points"]
        assert len(points) <= 400
        assert sum(p["count"] for p in points) == 500
        assert sum(p["anomaly_count"] for p in points) == 10 == category["anomaly_count"]
        for point in points:
            start = datetime.fromisoformat(point["bucket_start"])
            assert start.timestamp() % 900 == 0
            assert point["min"] <= point["avg"] <= point["max"]
            if point["anomaly_count"] and category["range"]:
                assert point["max"] > category["range"]["max"]  # Not averaged away.
    assert body["summary"]["anomaly_count"] == 40
    assert body["summary"]["anomalous_category_count"] == 4
    assert len(body["recent_anomalies"]) == 20


def test_custom_range_with_no_data(client):
    body = call(client, "GET", RANGE, "viewer1",
                params={"start": "2019-01-01T00:00:00Z", "end": "2019-01-02T00:00:00Z"}).json()
    assert body["summary"]["last_reading_at"] is None
    assert all(c["points"] == [] and c["latest"] is None for c in body["categories"])
    assert body["recent_anomalies"] == []


def test_exactly_three_months_is_allowed_and_more_is_refused(client):
    ok = call(client, "GET", RANGE, "viewer1",
              params={"start": "2025-11-30T00:00:00+08:00", "end": "2026-02-28T00:00:00+08:00"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["aggregation"]["bucket_seconds"] in (None, 21600)
    refused = call(client, "GET", RANGE, "viewer1",
                   params={"start": "2025-11-30T00:00:00+08:00", "end": "2026-02-28T00:00:01+08:00"})
    error = assert_error(refused, 422, "RANGE_TOO_LONG")
    assert error["message"] == "時間範圍超過 3 個月上限。請縮短範圍，或分批查詢。"
    assert error["details"] == {"max_months": 3, "latest_allowed_end": "2026-02-28T00:00:00+08:00"}


@pytest.mark.parametrize(
    "params, field",
    [
        ({"start": "2026-01-02T00:00:00Z", "end": "2026-01-01T00:00:00Z"}, "query.end"),
        ({"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"}, "query.end"),
        ({"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00Z"}, "query.start"),
        ({"start": "1700000000", "end": "2026-01-02T00:00:00Z"}, "query.start"),
        ({"end": "2026-01-02T00:00:00Z"}, "query.start"),
    ],
)
def test_custom_range_validation(client, params, field):
    error = assert_error(call(client, "GET", RANGE, "viewer1", params=params), 422, "VALIDATION_ERROR")
    assert field in {problem["field"] for problem in error["details"]}
