"""System log handlers and the batch writer, without a database (PRD-14)."""

import asyncio
import logging
import time

from sqlalchemy.exc import OperationalError

from app.models.system_log import Severity
from app.syslog.handler import AuditHandler, SystemErrorHandler
from app.syslog.writer import SyslogWriter


class FakeDatabase:
    """Stands in for a session factory. Refuses writes while `down` is true."""

    def __init__(self):
        self.down = False
        self.rows = []
        self.attempts = 0

    def __call__(self):
        return FakeSession(self)


class FakeSession:
    def __init__(self, db):
        self.db = db
        self.pending = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, rows):
        self.db.attempts += 1
        if self.db.down:
            raise OperationalError("insert", {}, ConnectionRefusedError("database down"))
        self.pending = list(rows)

    async def commit(self):
        self.db.rows.extend(self.pending)


def row(n):
    return {"n": n}


def collect(handler_cls, logger_name, **kwargs):
    rows = []
    handler = handler_cls(rows.append, **kwargs)
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    return rows, handler, logger


# --- Writer ---


def test_rows_wait_while_the_database_is_down_and_are_written_in_order_after():
    async def scenario():
        db = FakeDatabase()
        writer = SyslogWriter(db, flush_seconds=0.05)
        await writer.start()
        db.down = True
        for n in range(5):
            writer.submit(row(n))
        await asyncio.sleep(0.3)
        assert db.rows == [] and writer.buffered_count == 5 and db.attempts >= 1
        db.down = False
        for n in range(5, 8):
            writer.submit(row(n))
        # Backoff is at least 1 s after a failure.
        for _ in range(60):
            if len(db.rows) == 8:
                break
            await asyncio.sleep(0.05)
        await writer.stop()
        return db, writer

    db, writer = asyncio.run(scenario())
    assert [r["n"] for r in db.rows] == list(range(8))
    assert writer.buffered_count == 0 and writer.dropped_count == 0


def test_a_full_buffer_drops_the_oldest_rows_and_keeps_the_newest(caplog):
    async def scenario():
        db = FakeDatabase()
        db.down = True
        writer = SyslogWriter(db, buffer_limit=3)
        with caplog.at_level(logging.WARNING, logger="app.syslog"):
            for n in range(5):
                writer.submit(row(n))
        db.down = False
        assert await writer.flush()
        return db, writer

    db, writer = asyncio.run(scenario())
    assert [r["n"] for r in db.rows] == [2, 3, 4]
    assert writer.dropped_count == 2
    warnings = [r for r in caplog.records if r.name == "app.syslog" and "dropped" in r.getMessage()]
    assert len(warnings) == 1


def test_stop_writes_what_is_still_buffered():
    async def scenario():
        db = FakeDatabase()
        writer = SyslogWriter(db, flush_seconds=60)
        await writer.start()
        writer.submit(row(1))
        writer.submit(row(2))
        await writer.stop()
        return db

    assert [r["n"] for r in asyncio.run(scenario()).rows] == [1, 2]


def test_a_batch_wakes_the_writer_before_the_timer():
    async def scenario():
        db = FakeDatabase()
        writer = SyslogWriter(db, flush_seconds=60, batch_size=3)
        await writer.start()
        for n in range(3):
            writer.submit(row(n))
        await asyncio.sleep(0.2)
        written = len(db.rows)
        await writer.stop()
        return written

    assert asyncio.run(scenario()) == 3


# --- Audit handler ---


def test_only_r6_events_are_kept_and_secrets_are_left_out():
    rows, handler, logger = collect(AuditHandler, "test.syslog.audit")
    try:
        logger.info("account registered", extra={"event_type": "user_registered", "user_id": 1, "username": "a"})
        logger.info("category defaults seeded", extra={"event_type": "category_defaults_seeded"})
        logger.warning(
            "login rejected",
            extra={"event_type": "login_failure", "user_id": 7, "username": "bob", "reason": "wrong_password",
                   "password": "hunter2", "access_token": "abc"},
        )
        logger.info("record updated", extra={"event_type": "record_updated", "user_id": 7, "username": "bob",
                                             "record_id": 12, "fields": ["title", "value"]})
    finally:
        logger.removeHandler(handler)

    assert [r["event_name"] for r in rows] == ["login_failure", "record_updated"]
    failure, update = rows
    assert failure["event_type"] == "login_failure" and failure["severity"] is Severity.WARNING
    assert failure["user_id"] == 7 and failure["username"] == "bob"
    assert failure["summary"] == "登入失敗：密碼錯誤"
    assert failure["details"] == {"reason": "wrong_password"}
    assert "hunter2" not in repr(rows) and "abc" not in repr(rows)
    assert update["summary"] == "修改記錄 #12（欄位：title、value）"
    assert update["severity"] is Severity.INFO


def test_import_and_range_events_are_grouped():
    rows, handler, logger = collect(AuditHandler, "test.syslog.audit2")
    try:
        logger.warning("x", extra={"event_type": "records_import_rejected", "user_id": 1, "username": "u",
                                   "import_filename": "a.txt", "reason": "unsupported_format"})
        logger.info("x", extra={"event_type": "category_unit_cleared", "user_id": 1, "username": "u",
                                "category": "溫度", "old_unit": "°C", "new_unit": None})
    finally:
        logger.removeHandler(handler)
    assert [(r["event_type"], r["event_name"]) for r in rows] == [
        ("import_result", "records_import_rejected"),
        ("range_unit_changed", "category_unit_cleared"),
    ]
    assert rows[0]["summary"] == "a.txt：導入被拒絕（不支援的格式）"
    assert rows[1]["summary"] == "清除「溫度」的單位（原為 「°C」）"


# --- System errors ---


def test_errors_keep_the_exception_class_but_never_its_text():
    rows, handler, logger = collect(SystemErrorHandler, "test.syslog.errors")
    try:
        try:
            raise ValueError("secret-row-content")
        except ValueError:
            logger.exception("unhandled %s", "secret-arg", extra={"method": "GET", "path": "/x", "status": 500})
    finally:
        logger.removeHandler(handler)
    (kept,) = rows
    assert kept["event_type"] == "system_error" and kept["severity"] is Severity.ERROR
    assert kept["user_id"] is None and kept["username"] is None
    assert kept["details"] == {"logger": "test.syslog.errors", "exception": "ValueError", "method": "GET",
                               "path": "/x", "status": 500}
    assert "secret" not in repr(kept)
    assert kept["summary"] == "系統錯誤：unhandled %s"


def test_same_error_is_throttled_and_the_next_one_counts_the_suppressed():
    rows, handler, logger = collect(SystemErrorHandler, "test.syslog.throttle", throttle_seconds=0.2)
    try:
        for _ in range(4):
            logger.error("database error")
        logger.error("another error")
        time.sleep(0.25)
        logger.error("database error")
    finally:
        logger.removeHandler(handler)
    assert [r["summary"] for r in rows] == [
        "系統錯誤：database error",
        "系統錯誤：another error",
        "系統錯誤：database error",
    ]
    assert rows[2]["details"]["suppressed_count"] == 3


def test_warnings_count_only_from_the_simulator_and_live_hub():
    rows = []
    handler = SystemErrorHandler(rows.append)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        logging.getLogger("app.simulator").warning("simulator batch write failed; keeping readings to retry",
                                                   extra={"buffered": 8})
        logging.getLogger("app.records").warning("not a system problem")
        logging.getLogger("app.audit").warning("login rejected", extra={"event_type": "login_failure"})
        logging.getLogger("app.syslog").error("system log write failed")
        logging.getLogger("app.simulator").info("simulator started")
    finally:
        root.removeHandler(handler)
    (kept,) = rows
    assert kept["severity"] is Severity.WARNING
    assert kept["details"] == {"logger": "app.simulator", "buffered": 8}
