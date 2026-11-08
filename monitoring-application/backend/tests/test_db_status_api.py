"""Database status (PRD-15) against a real MariaDB. Run through tests/run_integration.sh.

The app here has no lifespan (no simulator), and one TestClient keeps one event loop for the
whole module, so the pooled engine under test stays usable between requests.
"""

import asyncio
import os
import re
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.router import api_router
from app.auth.service import register_user
from app.core.config import load_settings
from app.core.errors import register_error_handlers
from app.db.migrate import upgrade_to_head
from app.db.session import create_pooled_engine, create_unpooled_engine, get_session
from app.dbstatus import service
from app.dbstatus.schemas import PoolUsage
from app.dbstatus.service import DbStatusMonitor, categorize, get_monitor, get_status_engine, utc_now
from app.models.record import Record
from app.models.record_import import RecordImport
from app.models.user import Role, User
from app.prestart import _wait_for_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

PASSWORD = "Str0ng!pass"
STATUS = "/api/v1/db-status"
ACCOUNTS = (("dbs_admin", Role.ADMIN), ("dbs_user", Role.USER), ("dbs_viewer", Role.VIEWER))


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


async def _clear(session):
    await session.execute(delete(RecordImport))
    await session.execute(delete(Record))
    await session.execute(delete(User))
    await session.commit()


def add_records(*rows):
    """rows: (timestamp, creator username or None)."""

    async def work(session):
        ids = {u.username: u.id for u in (await session.scalars(select(User))).all()}
        for timestamp, creator in rows:
            session.add(
                Record(
                    title="狀態測試",
                    value=1.0,
                    category="lb335-check",
                    timestamp=timestamp,
                    created_by_user_id=ids[creator] if creator else None,
                    is_anomaly=False,
                )
            )
        await session.commit()

    run_in_db(work)


class Api:
    http: TestClient
    app: FastAPI
    headers: dict


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

    async def prepare(session):
        await _clear(session)
        for username, role in ACCOUNTS:
            user = await register_user(session, username, PASSWORD)
            user.role = role
        await session.commit()

    async def migrate():
        db = create_unpooled_engine(Env.settings)
        try:
            await _wait_for_database(db, 90)
            await upgrade_to_head(db)
        finally:
            await db.dispose()

    asyncio.run(migrate())
    run_in_db(prepare)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_router)
    auth_factory = async_sessionmaker(create_unpooled_engine(Env.settings), expire_on_commit=False)

    async def session_override():
        async with auth_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app, raise_server_exceptions=False) as http:
        api = Api()
        api.http, api.app, api.headers = http, app, {}
        for username, _ in ACCOUNTS:
            response = http.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
            assert response.status_code == 200, response.text
            api.headers[username] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        yield api
    run_in_db(_clear)


@pytest.fixture
def setup(api):
    """A fresh monitor and a fresh pooled engine (built in the app's loop) for every test."""
    run_in_db(lambda session: _wipe_records(session))
    initial = create_pooled_engine(Env.settings)
    state = {"monitor": DbStatusMonitor(), "engine": initial}
    api.app.dependency_overrides[get_monitor] = lambda: state["monitor"]
    api.app.dependency_overrides[get_status_engine] = lambda: state["engine"]
    yield api, state
    for engine in {id(e): e for e in (initial, state["engine"])}.values():
        api.http.portal.call(engine.dispose)
    api.app.dependency_overrides.pop(get_monitor, None)
    api.app.dependency_overrides.pop(get_status_engine, None)


async def _wipe_records(session):
    await session.execute(delete(Record))
    await session.commit()


def at(value):
    return datetime.fromisoformat(value)


def get_status(api, who="dbs_admin"):
    return api.http.get(STATUS, headers=api.headers[who] if who else {})


# --- Roles (PRD-15-R1) ---


def test_admin_only(setup):
    api, _ = setup
    assert get_status(api).status_code == 200
    for who in ("dbs_user", "dbs_viewer"):
        response = get_status(api, who)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
    response = get_status(api, None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


# --- Available (PRD-15-R2, R3) ---


def test_available_metrics(setup):
    api, _ = setup
    now = utc_now()
    newest_system = now - timedelta(seconds=10)
    add_records(
        # In time order, as the simulator writes them.
        (now - timedelta(seconds=70), None),
        (now - timedelta(seconds=40), None),
        (newest_system, None),
        # Manual records, newer than any system record, are not batch writes.
        (now + timedelta(days=1), "dbs_user"),
        (now, "dbs_admin"),
    )
    began = time.perf_counter()
    response = get_status(api)
    elapsed = time.perf_counter() - began
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "available"
    assert body["error"] is None
    assert 0 <= body["response_time_ms"] < 250
    assert body["record_count"] == 5
    assert body["record_count_as_of"] is not None
    assert abs(at(body["last_batch_write_at"]) - newest_system) < timedelta(milliseconds=1)
    assert body["last_available_at"] == body["checked_at"]
    assert body["refresh_interval_seconds"] == 10
    assert body["warnings"] == []
    pool = body["pool"]
    assert pool["capacity"] == Env.settings.db_pool_size + Env.settings.db_max_overflow
    assert pool["open"] == pool["in_use"] + pool["idle"]
    assert pool["in_use"] == 0  # The probe's own connection is back in the pool.
    assert pool["idle"] >= 1
    assert elapsed < 2.0
    # Every call is a new check.
    again = get_status(api).json()
    assert at(again["checked_at"]) > at(body["checked_at"])


def test_no_system_records_yet(setup):
    api, _ = setup
    add_records((utc_now(), "dbs_user"))
    body = get_status(api).json()
    assert body["status"] == "available"
    assert body["last_batch_write_at"] is None
    assert body["record_count"] == 1


def test_record_count_is_cached(setup):
    api, state = setup
    add_records((utc_now(), None))
    first = get_status(api).json()
    assert first["record_count"] == 1
    add_records((utc_now(), None), (utc_now(), None))
    cached = get_status(api).json()
    assert cached["record_count"] == 1
    assert cached["record_count_as_of"] == first["record_count_as_of"]
    # The newest batch time is not cached.
    assert at(cached["last_batch_write_at"]) > at(first["last_batch_write_at"])
    state["monitor"] = DbStatusMonitor()
    assert get_status(api).json()["record_count"] == 3


def test_stale_batch_warning(setup):
    api, _ = setup
    add_records((utc_now() - timedelta(hours=1), None))
    body = get_status(api).json()
    assert [w["code"] for w in body["warnings"]] == ["BATCH_STALE"]


# --- Unavailable after login (PRD-15-R4) ---

_LEAK = re.compile(r"Can't|Errno|refused|2003|127\.0\.0\.1|Traceback", re.IGNORECASE)


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def assert_unavailable(body, category):
    assert body["status"] == "unavailable"
    assert body["error"] == {"category": category, "message": service.ERROR_MESSAGES[category]}
    for field in ("response_time_ms", "record_count", "record_count_as_of", "last_batch_write_at"):
        assert body[field] is None
    assert body["warnings"] == []
    assert body["checked_at"]


def test_unreachable_database_answers_200(setup):
    api, state = setup
    assert get_status(api).json()["status"] == "available"
    state["engine"] = create_pooled_engine(Env.settings.model_copy(update={"db_host": "127.0.0.1", "db_port": _free_port()}))
    response = get_status(api)
    assert response.status_code == 200
    assert_unavailable(response.json(), "unreachable")
    assert response.json()["last_available_at"] is not None
    assert not _LEAK.search(response.text)


def test_hung_database_is_bounded(setup):
    api, state = setup
    # Accepts TCP connections (the kernel completes them) but never sends the server greeting.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    try:
        port = listener.getsockname()[1]
        state["engine"] = create_pooled_engine(Env.settings.model_copy(update={"db_host": "127.0.0.1", "db_port": port}))
        began = time.perf_counter()
        response = get_status(api)
        elapsed = time.perf_counter() - began
    finally:
        listener.close()
    assert response.status_code == 200
    assert_unavailable(response.json(), "timeout")
    assert response.json()["last_available_at"] is None
    assert elapsed < service.PROBE_TIMEOUT_SECONDS + 1.5


def test_pool_exhausted(setup, monkeypatch):
    api, state = setup
    monkeypatch.setenv("DB_POOL_SIZE", "1")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    # The endpoint reads the capacity from the environment; the engine gets the same limits.
    engine = create_pooled_engine(Env.settings.model_copy(update={"db_pool_size": 1, "db_max_overflow": 0}))
    state["engine"] = engine
    held = api.http.portal.call(engine.connect().start)
    try:
        response = get_status(api)
    finally:
        api.http.portal.call(held.close)
    assert response.status_code == 200
    body = response.json()
    assert_unavailable(body, "pool_exhausted")
    assert body["pool"] == {"in_use": 1, "idle": 0, "open": 1, "capacity": 1, "usage_ratio": 1.0}
    assert get_status(api).json()["status"] == "available"


def test_database_down_at_login_check_is_503(setup):
    """Login validity is read from the database, so with it down the shared auth answers 503."""
    api, _ = setup
    down = create_unpooled_engine(Env.settings.model_copy(update={"db_host": "127.0.0.1", "db_port": _free_port()}))
    factory = async_sessionmaker(down, expire_on_commit=False)

    async def down_session():
        async with factory() as session:
            yield session

    previous = api.app.dependency_overrides[get_session]
    api.app.dependency_overrides[get_session] = down_session
    try:
        response = get_status(api)
    finally:
        api.app.dependency_overrides[get_session] = previous
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert not _LEAK.search(response.text)


# --- Pure helpers ---


def _operational(code):
    return OperationalError("SELECT 1", {}, Exception(code, "secret detail"))


@pytest.mark.parametrize(
    ("exc", "pool_full", "expected"),
    [
        (TimeoutError(), False, "timeout"),
        (TimeoutError(), True, "pool_exhausted"),
        (PoolTimeoutError(), False, "pool_exhausted"),
        (_operational(2003), False, "unreachable"),
        (_operational(2013), False, "unreachable"),
        (_operational(1045), False, "auth_failed"),
        (_operational(1146), False, "error"),
        (RuntimeError("boom"), False, "error"),
    ],
)
def test_categorize(exc, pool_full, expected):
    assert categorize(exc, pool_full=pool_full) == expected


def test_warning_thresholds():
    monitor = DbStatusMonitor()
    now = utc_now()
    quiet = PoolUsage(in_use=23, idle=0, open=23, capacity=30, usage_ratio=23 / 30)
    busy = PoolUsage(in_use=24, idle=0, open=24, capacity=30, usage_ratio=0.8)
    assert monitor._warnings(249.9, quiet, now - timedelta(seconds=90), now, 30) == []
    codes = [w.code for w in monitor._warnings(250.0, busy, now - timedelta(seconds=91), now, 30)]
    assert codes == ["SLOW_RESPONSE", "POOL_HIGH", "BATCH_STALE"]


def test_batch_stale_threshold_follows_the_flush_cadence_for_a_fast_simulator():
    """At one reading per second, batches still land every 5 s, so stale means over 15 s."""
    monitor = DbStatusMonitor()
    now = utc_now()
    quiet = PoolUsage(in_use=1, idle=0, open=1, capacity=30, usage_ratio=1 / 30)
    assert monitor._warnings(1.0, quiet, now - timedelta(seconds=15), now, 1) == []
    [warning] = monitor._warnings(1.0, quiet, now - timedelta(seconds=16), now, 1)
    assert warning.code == "BATCH_STALE" and "15 秒" in warning.message


def test_no_raw_sql():
    root = Path(service.__file__).resolve().parents[1]
    for path in [*root.joinpath("dbstatus").glob("*.py"), root / "api" / "v1" / "db_status.py"]:
        source = path.read_text(encoding="utf-8")
        assert "text(" not in source and "exec_driver_sql" not in source, path
