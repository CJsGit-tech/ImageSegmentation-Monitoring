"""Bulk import checks (LB-328, PRD-06) against a real MariaDB. Run through tests/run_integration.sh.

Rows are generated here; the user's sample files are not part of the suite.
"""

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import anyio
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
from app.models.record_import import RecordImport
from app.models.user import Role, User
from app.prestart import _wait_for_database
from app.records import importing

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_HOST"),
    reason="needs MariaDB: run backend/tests/run_integration.sh (TEST_DB_HOST is not set)",
)

GOOD_PASSWORD = "Str0ng!pass"
LOGIN = "/api/v1/auth/login"
RECORDS = "/api/v1/records"
IMPORTS = "/api/v1/records/imports"
HEADER = "title,value,category,timestamp"
CATEGORY_PREFIX = "imp-"


class Env:
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


async def _clear(session):
    await session.execute(delete(RecordImport))
    await session.execute(delete(Record))
    await session.execute(delete(CategorySetting).where(CategorySetting.category.startswith(CATEGORY_PREFIX)))
    await session.commit()


def counts():
    async def work(session):
        records = await session.scalar(select(func.count()).select_from(Record))
        imports = await session.scalar(select(func.count()).select_from(RecordImport))
        return records, imports

    return run_in_db(work)


def set_range(category, low, high):
    async def work(session):
        setting = await session.get(CategorySetting, category)
        if setting is None:
            setting = CategorySetting(category=category)
            session.add(setting)
        setting.range_min, setting.range_max = low, high
        await session.commit()

    run_in_db(work)


def _session_factory():
    # NullPool: requests from several threads each run on their own event loop.
    factory = async_sessionmaker(engine(), expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    return session_override


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
                await _clear(session)
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

    app = create_app()
    app.dependency_overrides[get_session] = _session_factory()

    class Api:
        http = TestClient(app, raise_server_exceptions=False)
        headers = {}
        ids = {}

    async def load_ids(session):
        return {user.username: user.id for user in await session.scalars(select(User))}

    Api.ids = run_in_db(load_ids)
    yield Api

    async def clear_all(session):
        await _clear(session)
        await session.execute(delete(User))
        await session.commit()

    run_in_db(clear_all)


@pytest.fixture
def client(api):
    run_in_db(_clear)
    for username in api.ids:
        response = api.http.post(LOGIN, json={"username": username, "password": GOOD_PASSWORD})
        assert response.status_code == 200, response.text
        api.headers[username] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    yield api
    run_in_db(_clear)


def upload(api, filename, content, who="user1", http=None):
    if isinstance(content, str):
        content = content.encode("utf-8")
    headers = api.headers[who] if who else {}
    return (http or api.http).post(IMPORTS, headers=headers, files={"file": (filename, content, "application/octet-stream")})


def call(api, method, path, who, **kwargs):
    return api.http.request(method, path, headers=api.headers[who], **kwargs)


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code, response.text
    return error


def csv_text(rows, header=HEADER, bom=False):
    text = "\n".join([header, *rows]) + "\n"
    return ("\ufeff" if bom else "") + text


def generated_rows(count, category=f"{CATEGORY_PREFIX}溫度"):
    start = datetime(2026, 9, 1, tzinfo=UTC)
    return [
        {
            "title": f"溫度感測器 T-{index % 3 + 1:02d} 定期檢測",
            "value": round(20 + (index * 7.3) % 60, 1),
            "category": category,
            "timestamp": (start + timedelta(minutes=index)).isoformat(),
        }
        for index in range(count)
    ]


def as_csv(rows):
    return csv_text(f"{r['title']},{r['value']},{r['category']},{r['timestamp']}" for r in rows)


def list_all(api, who="viewer1", **params):
    response = call(api, "GET", RECORDS, who, params={"limit": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()


# --- Valid files ---


def test_csv_import_saves_every_row_with_the_importer_as_creator(client):
    rows = generated_rows(5)
    response = upload(client, "readings.csv", as_csv(rows))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["imported_count"] == 5
    assert body["filename"] == "readings.csv"
    assert body["format"] == "csv"
    assert body["creator_id"] == client.ids["user1"]
    assert body["creator_name"] == "user1"
    page = list_all(client, category=f"{CATEGORY_PREFIX}溫度", sort_by="timestamp", order="asc")
    assert page["total"] == 5
    assert {item["creator_id"] for item in page["items"]} == {client.ids["user1"]}
    assert [item["value"] for item in page["items"]] == [row["value"] for row in rows]
    assert counts() == (5, 1)


def test_json_import_saves_every_row_with_the_importer_as_creator(client):
    rows = generated_rows(4)
    rows[0]["value"] = 1798  # integers are numbers too
    response = upload(client, "readings.json", json.dumps(rows, ensure_ascii=False), who="admin1")
    assert response.status_code == 201, response.text
    assert response.json()["imported_count"] == 4
    assert response.json()["format"] == "json"
    page = list_all(client, category=f"{CATEGORY_PREFIX}溫度")
    assert page["total"] == 4
    assert {item["creator_id"] for item in page["items"]} == {client.ids["admin1"]}


def test_missing_timestamps_are_the_import_time_and_given_ones_keep_their_instant(client):
    before = datetime.now(UTC)
    text = csv_text([f"a,1,{CATEGORY_PREFIX}c,", f"b,2,{CATEGORY_PREFIX}c,2026-09-01T00:14:42.5+08:00"])
    body = upload(client, "times.csv", text).json()
    stamps = {i["title"]: datetime.fromisoformat(i["timestamp"]) for i in list_all(client)["items"]}
    assert before - timedelta(seconds=1) <= stamps["a"] <= datetime.now(UTC)
    assert stamps["a"] == datetime.fromisoformat(body["imported_at"])
    assert stamps["b"] == datetime(2026, 8, 31, 16, 14, 42, 500000, tzinfo=UTC)

    json_rows = [{"title": "c", "value": 1, "category": f"{CATEGORY_PREFIX}c"},
                 {"title": "d", "value": 1, "category": f"{CATEGORY_PREFIX}c", "timestamp": None}]
    assert upload(client, "times.json", json.dumps(json_rows)).status_code == 201


def test_csv_accepts_bom_any_column_order_no_timestamp_column_and_skips_blank_lines(client):
    text = "\ufeffcategory, value ,title\n" + f"{CATEGORY_PREFIX}c,1.5,a\n\n,,\n{CATEGORY_PREFIX}c,-2e3,b\n"
    response = upload(client, "order.csv", text)
    assert response.status_code == 201, response.text
    assert response.json()["imported_count"] == 2
    assert sorted(i["value"] for i in list_all(client)["items"]) == [-2000.0, 1.5]


def test_text_is_trimmed_and_quoted_cells_may_hold_commas(client):
    text = csv_text([f'"  溫度, 一號  ",1,"　{CATEGORY_PREFIX}c ",'])
    assert upload(client, "trim.csv", text).status_code == 201
    item = list_all(client)["items"][0]
    assert (item["title"], item["category"]) == ("溫度, 一號", f"{CATEGORY_PREFIX}c")


def test_duplicate_rows_in_one_file_are_all_imported(client):
    row = f"same,1,{CATEGORY_PREFIX}c,2026-09-01T00:00:00+08:00"
    assert upload(client, "dupes.csv", csv_text([row, row, row])).json()["imported_count"] == 3
    assert counts() == (3, 1)


# --- Anomalies (PRD-09-R10, PRD-18-R6) ---


def test_out_of_range_rows_are_saved_as_anomalies_without_confirmation(client):
    ranged, unranged = f"{CATEGORY_PREFIX}壓力", f"{CATEGORY_PREFIX}新分類"
    set_range(ranged, 10, 20)
    text = csv_text([f"low,10,{ranged},", f"mid,15,{ranged},", f"high,20,{ranged},", f"far,999,{unranged},"])
    response = upload(client, "anomalies.csv", text)
    assert response.status_code == 201, response.text
    assert response.json()["anomaly_count"] == 2
    flags = {i["title"]: i["is_anomaly"] for i in list_all(client)["items"]}
    assert flags == {"low": True, "mid": False, "high": True, "far": False}
    assert list_all(client, anomaly_only="true")["total"] == 2


# --- Refused files: nothing is written ---


def test_one_bad_row_refuses_the_whole_file_with_row_number_and_reason(client):
    rows = [f"ok,1,{CATEGORY_PREFIX}c,"] * 3 + [f"bad,abc,{CATEGORY_PREFIX}c,"] + [f"ok,1,{CATEGORY_PREFIX}c,"]
    error = assert_error(upload(client, "bad.csv", csv_text(rows)), 422, "IMPORT_ROWS_INVALID")
    # Header is row 1, so the fourth data row is row 5.
    assert error["details"]["errors"] == [{"row": 5, "field": "value", "message": "必須是數字", "type": "float_type"}]
    assert error["details"]["error_count"] == 1
    assert error["details"]["truncated"] is False
    assert error["details"]["filename"] == "bad.csv"
    assert counts() == (0, 0)


@pytest.mark.parametrize(
    ("row", "field", "kind"),
    [
        (f",1,{CATEGORY_PREFIX}c,", "title", "missing"),
        (f"t,,{CATEGORY_PREFIX}c,", "value", "missing"),
        ("t,1,,", "category", "missing"),
        (f"t,nan,{CATEGORY_PREFIX}c,", "value", "float_type"),
        (f"t,inf,{CATEGORY_PREFIX}c,", "value", "float_type"),
        (f"t,1e999,{CATEGORY_PREFIX}c,", "value", "finite_number"),
        (f"t,1_000,{CATEGORY_PREFIX}c,", "value", "float_type"),
        (f't,"1,000",{CATEGORY_PREFIX}c,', "value", "float_type"),
        (f"t,1,{CATEGORY_PREFIX}c,2026-09-01T00:14:42", "timestamp", "timezone_aware"),
        (f"t,1,{CATEGORY_PREFIX}c,1717200000", "timestamp", "value_error"),
        (f"t,1,{CATEGORY_PREFIX}c,yesterday", "timestamp", "value_error"),
        (f"{'x' * 256},1,{CATEGORY_PREFIX}c,", "title", "string_too_long"),
        (f'"a\nb",1,{CATEGORY_PREFIX}c,', "title", "value_error"),
        (f"a\x00,1,{CATEGORY_PREFIX}c,", "title", "value_error"),
    ],
)
def test_csv_row_rules_match_the_single_record_api(client, row, field, kind):
    error = assert_error(upload(client, "rule.csv", csv_text([row])), 422, "IMPORT_ROWS_INVALID")
    problem = error["details"]["errors"][0]
    assert (problem["row"], problem["field"], problem["type"]) == (2, field, kind)
    assert problem["message"]
    assert counts() == (0, 0)


def test_csv_row_numbers_count_records_not_lines(client):
    rows = [f'"multi\nline",1,{CATEGORY_PREFIX}c,', f"bad,x,{CATEGORY_PREFIX}c,"]
    error = assert_error(upload(client, "lines.csv", csv_text(rows)), 422, "IMPORT_ROWS_INVALID")
    assert [(p["row"], p["field"]) for p in error["details"]["errors"]] == [(2, "title"), (3, "value")]


def test_csv_wrong_cell_count_is_a_row_error(client):
    rows = [f"a,1,{CATEGORY_PREFIX}c,,extra", "b,1"]
    error = assert_error(upload(client, "cells.csv", csv_text(rows)), 422, "IMPORT_ROWS_INVALID")
    assert [(p["row"], p["field"], p["type"]) for p in error["details"]["errors"]] == [
        (2, None, "column_count"),
        (3, None, "column_count"),
    ]


@pytest.mark.parametrize(
    ("item", "field", "kind"),
    [
        ({"title": "t", "value": "1", "category": "c"}, "value", "float_type"),
        ({"title": "t", "value": True, "category": "c"}, "value", "float_type"),
        ({"title": "t", "value": 1, "category": "c", "unit": "°C"}, "unit", "extra_forbidden"),
        ({"title": "t", "value": 1, "category": "c", "creator": "x"}, "creator", "extra_forbidden"),
        ({"title": "t", "value": 1, "category": "c", "timestamp": ""}, "timestamp", "value_error"),
        ({"title": "t", "value": 1, "category": "c", "timestamp": 1717200000}, "timestamp", "value_error"),
        ({"title": "t", "value": 1}, "category", "missing"),
        ({"title": 5, "value": 1, "category": "c"}, "title", "string_type"),
    ],
)
def test_json_row_rules(client, item, field, kind):
    good = {"title": "ok", "value": 1, "category": f"{CATEGORY_PREFIX}c"}
    error = assert_error(upload(client, "rule.json", json.dumps([good, item])), 422, "IMPORT_ROWS_INVALID")
    problem = error["details"]["errors"][0]
    # JSON rows are numbered by their position in the array, from 1.
    assert (problem["row"], problem["field"], problem["type"]) == (2, field, kind)
    assert counts() == (0, 0)


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ('[{"title":"t","value":1e999,"category":"c"}]', "finite_number"),
        ('[{"title":"\\ud800","value":1,"category":"c"}]', "string_unicode"),
        ('[{"title":"a","title":"b","value":1,"category":"c"}]', "duplicate_key"),
        ("[5]", "object_type"),
    ],
)
def test_json_raw_row_problems(client, raw, kind):
    error = assert_error(upload(client, "raw.json", raw), 422, "IMPORT_ROWS_INVALID")
    assert error["details"]["errors"][0]["type"] == kind
    assert error["details"]["errors"][0]["row"] == 1


def test_errors_are_capped_but_counted(client):
    rows = [f"t,x,{CATEGORY_PREFIX}c,"] * 250
    error = assert_error(upload(client, "many.csv", csv_text(rows)), 422, "IMPORT_ROWS_INVALID")
    assert error["details"]["error_count"] == 250
    assert error["details"]["truncated"] is True
    assert len(error["details"]["errors"]) == importing.MAX_REPORTED_ERRORS
    assert error["details"]["errors"][0]["row"] == 2


@pytest.mark.parametrize(
    ("filename", "content", "reason"),
    [
        ("big5.csv", "title,value,category\n溫度,1,c\n".encode("big5"), "not_utf8"),
        ("bad.json", "[{", "invalid_json"),
        ("nan.json", '[{"title":"t","value":NaN,"category":"c"}]', "invalid_json"),
        ("object.json", '{"title":"t","value":1,"category":"c"}', "not_array"),
        ("quote.csv", 'title,value,category\n"open,1,c\n', "csv_malformed"),
        ("unit.csv", "title,value,category,unit\nt,1,c,°C\n", "unknown_columns"),
        ("short.csv", "title,value\nt,1\n", "missing_columns"),
        ("twice.csv", "title,value,category,title\nt,1,c,t\n", "duplicate_columns"),
        ("garbage.json", b"\x89PNG\r\n\x1a\n", "not_utf8"),
    ],
)
def test_unparseable_files_are_refused(client, filename, content, reason):
    error = assert_error(upload(client, filename, content), 422, "IMPORT_FILE_UNPARSEABLE")
    assert error["details"]["reason"] == reason
    assert error["message"]
    assert counts() == (0, 0)


@pytest.mark.parametrize("filename", ["data.xlsx", "data.txt", "data", "data.csv.exe"])
def test_other_formats_are_refused(client, filename):
    error = assert_error(upload(client, filename, "title,value,category\nt,1,c\n"), 415, "IMPORT_UNSUPPORTED_FORMAT")
    assert error["details"]["accepted"] == [".csv", ".json"]
    assert counts() == (0, 0)


@pytest.mark.parametrize(
    ("filename", "content"),
    [("e.csv", ""), ("e.csv", "title,value,category,timestamp\n"), ("e.csv", "\ufeff\n\n"), ("e.json", "[]"), ("e.json", "  \n")],
)
def test_empty_files_are_refused_and_do_not_use_up_the_name(client, filename, content):
    assert_error(upload(client, filename, content), 422, "IMPORT_FILE_EMPTY")
    assert counts() == (0, 0)
    valid = as_csv(generated_rows(1)) if filename.endswith(".csv") else json.dumps(generated_rows(1))
    assert upload(client, filename, valid).status_code == 201


def test_missing_file_part_and_blank_filename(client):
    response = client.http.post(IMPORTS, headers=client.headers["user1"], data={"other": "x"})
    assert_error(response, 422, "IMPORT_FILE_MISSING")
    response = client.http.post(IMPORTS, headers=client.headers["user1"], content=b"[]")
    assert_error(response, 422, "IMPORT_FILE_MISSING")
    assert_error(upload(client, "   ", "[]"), 422, "IMPORT_FILENAME_INVALID")


def test_size_and_row_limits(client):
    padding = "x" * 200
    line = f"{padding},1,{CATEGORY_PREFIX}c,"
    too_big = csv_text([line] * (importing.MAX_FILE_BYTES // len(line) + 10))
    error = assert_error(upload(client, "big.csv", too_big), 413, "IMPORT_FILE_TOO_LARGE")
    assert error["details"]["max_bytes"] == importing.MAX_FILE_BYTES

    rows = [f"t,1,{CATEGORY_PREFIX}c,"] * (importing.MAX_ROWS + 1)
    error = assert_error(upload(client, "rows.csv", csv_text(rows)), 422, "IMPORT_TOO_MANY_ROWS")
    assert error["details"] == {"filename": "rows.csv", "format": "csv", "max_rows": importing.MAX_ROWS, "row_count": importing.MAX_ROWS + 1}
    json_rows = [{"title": "t", "value": 1, "category": f"{CATEGORY_PREFIX}c"}] * (importing.MAX_ROWS + 1)
    assert_error(upload(client, "rows.json", json.dumps(json_rows)), 422, "IMPORT_TOO_MANY_ROWS")
    assert counts() == (0, 0)


# --- Filename rule (PRD-06-R7) ---


def test_fixed_file_can_be_reuploaded_under_the_same_name_without_duplicates(client):
    rows = generated_rows(6)
    broken = as_csv(rows).replace(str(rows[2]["value"]), "oops", 1)
    assert_error(upload(client, "fix-me.csv", broken), 422, "IMPORT_ROWS_INVALID")
    assert counts() == (0, 0)
    response = upload(client, "fix-me.csv", as_csv(rows))
    assert response.status_code == 201, response.text
    assert counts() == (6, 1)
    assert list_all(client)["total"] == 6


@pytest.mark.parametrize("again", ["daily.csv", "DAILY.CSV", "  Daily.csv  ", "C:\\fakepath\\daily.csv", "dir/daily.csv"])
def test_same_name_after_success_is_refused_and_a_new_name_works(client, again):
    rows = generated_rows(3)
    assert upload(client, "daily.csv", as_csv(rows)).status_code == 201
    error = assert_error(upload(client, again, as_csv(rows)), 409, "IMPORT_FILENAME_TAKEN")
    previous = error["details"]["previous_import"]
    assert set(previous) == {"imported_at", "row_count", "same_content"}
    assert previous["row_count"] == 3
    assert previous["same_content"] is True
    assert "user1" not in json.dumps(error)
    assert counts() == (3, 1)
    assert upload(client, "daily-2.csv", as_csv(rows)).status_code == 201
    assert counts() == (6, 2)


def test_filename_rule_is_global_and_reports_changed_content(client):
    assert upload(client, "shared.json", json.dumps(generated_rows(2)), who="user1").status_code == 201
    error = assert_error(upload(client, "Shared.JSON", json.dumps(generated_rows(3)), who="user2"), 409, "IMPORT_FILENAME_TAKEN")
    assert error["details"]["previous_import"]["same_content"] is False
    assert "user1" not in json.dumps(error)
    assert_error(upload(client, "shared.json", json.dumps(generated_rows(2)), who="admin1"), 409, "IMPORT_FILENAME_TAKEN")


def test_nfc_and_nfd_spellings_are_the_same_name(client):
    nfc, nfd = "caf\u00e9.csv", "cafe\u0301.csv"
    assert upload(client, nfc, as_csv(generated_rows(1))).status_code == 201
    assert_error(upload(client, nfd, as_csv(generated_rows(1))), 409, "IMPORT_FILENAME_TAKEN")


def test_simultaneous_uploads_of_one_name_write_once(api, monkeypatch):
    run_in_db(_clear)
    for username in ("user1", "user2"):
        response = api.http.post(LOGIN, json={"username": username, "password": GOOD_PASSWORD})
        api.headers[username] = {"Authorization": f"Bearer {response.json()['access_token']}"}

    # Hold both requests after their filename check until both have passed it, so that the
    # database's unique key, not the check, has to settle the race.
    barrier = threading.Barrier(2, timeout=30)
    original = importing._end_read_snapshot

    async def both_checked(session):
        await anyio.to_thread.run_sync(barrier.wait)
        await original(session)

    monkeypatch.setattr(importing, "_end_read_snapshot", both_checked)
    text = as_csv(generated_rows(50))
    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(upload, api, "race.csv", text, who) for who in ("user1", "user2")]
            responses = [future.result() for future in futures]
        statuses = sorted(r.status_code for r in responses)
        assert statuses == [201, 409], [r.text for r in responses]
        loser = next(r for r in responses if r.status_code == 409)
        assert loser.json()["error"]["code"] == "IMPORT_FILENAME_TAKEN"
        assert loser.json()["error"]["details"]["previous_import"]["same_content"] is True
        assert counts() == (50, 1)
    finally:
        run_in_db(_clear)


# --- Roles and what happens to imported records (PRD-06-R2, PRD-05) ---


def test_viewer_and_anonymous_are_refused_and_nothing_is_written(client):
    error = assert_error(upload(client, "v.csv", as_csv(generated_rows(2)), who="viewer1"), 403, "FORBIDDEN")
    assert error["details"]["required_capability"] == "records.import"
    assert_error(upload(client, "v.csv", as_csv(generated_rows(2)), who=None), 401, "UNAUTHORIZED")
    assert counts() == (0, 0)
    # The refused name is still free.
    assert upload(client, "v.csv", as_csv(generated_rows(2))).status_code == 201


def test_imported_records_follow_the_edit_and_delete_rules(client):
    ranged = f"{CATEGORY_PREFIX}振動"
    set_range(ranged, 0, 10)
    text = csv_text([f"mine,5,{ranged},", f"other,6,{ranged},", f"admin,7,{ranged},", f"flagged,10,{ranged},"])
    assert upload(client, "perm.csv", text).status_code == 201
    ids = {i["title"]: i["id"] for i in list_all(client)["items"]}

    patched = call(client, "PATCH", f"{RECORDS}/{ids['mine']}", "user1", json={"title": "mine2"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["creator_id"] == client.ids["user1"]
    assert call(client, "DELETE", f"{RECORDS}/{ids['mine']}", "user1").status_code == 204
    assert_error(call(client, "PATCH", f"{RECORDS}/{ids['other']}", "user2", json={"title": "x"}), 403, "NOT_RECORD_OWNER")
    assert_error(call(client, "DELETE", f"{RECORDS}/{ids['other']}", "user2"), 403, "NOT_RECORD_OWNER")
    assert_error(call(client, "DELETE", f"{RECORDS}/{ids['other']}", "viewer1"), 403, "FORBIDDEN")
    assert call(client, "PATCH", f"{RECORDS}/{ids['admin']}", "admin1", json={"value": 8}).status_code == 200
    assert call(client, "DELETE", f"{RECORDS}/{ids['admin']}", "admin1").status_code == 204
    assert_error(call(client, "DELETE", f"{RECORDS}/{ids['flagged']}", "user2"), 403, "NOT_RECORD_OWNER")
    assert call(client, "PATCH", f"{RECORDS}/{ids['flagged']}", "user1", json={"title": "flagged2"}).status_code == 200
    assert call(client, "DELETE", f"{RECORDS}/{ids['flagged']}", "admin1").status_code == 204


# --- Audit (PRD-14-R6) ---


def test_import_results_are_audited_without_row_contents(client, caplog):
    secret_title = "不應出現在日誌的標題"
    with caplog.at_level(logging.INFO, logger="app.audit"):
        assert upload(client, "audit.csv", csv_text([f"{secret_title},1,{CATEGORY_PREFIX}c,"])).status_code == 201
        assert upload(client, "audit-bad.csv", csv_text([f"{secret_title},x,{CATEGORY_PREFIX}c,"])).status_code == 422
        assert upload(client, "audit.csv", csv_text([f"{secret_title},1,{CATEGORY_PREFIX}c,"])).status_code == 409
        assert upload(client, "audit.csv", "x", who="viewer1").status_code == 403
    events = [r for r in caplog.records if r.name == "app.audit"]
    done = next(r for r in events if r.event_type == "records_imported")
    assert (done.import_filename, done.file_format, done.row_count, done.anomaly_count) == ("audit.csv", "csv", 1, 0)
    assert done.user_id == client.ids["user1"]
    assert isinstance(done.duration_ms, int)
    rejected = [(r.reason, r.error_count) for r in events if r.event_type == "records_import_rejected"]
    assert rejected == [("rows_invalid", 1), ("filename_taken", None)]
    assert any(r.event_type == "permission_denied" and r.capability == "records.import" for r in events)
    assert secret_title not in caplog.text
    for record in events:
        assert secret_title not in json.dumps(record.__dict__, default=str, ensure_ascii=False)


# --- Timing ---


@pytest.mark.parametrize("count", [1000, importing.MAX_ROWS])
def test_timing(client, count):
    rows = generated_rows(count)
    for name, content in ((f"t{count}.csv", as_csv(rows)), (f"t{count}.json", json.dumps(rows, ensure_ascii=False))):
        started = time.perf_counter()
        response = upload(client, name, content)
        elapsed = time.perf_counter() - started
        assert response.status_code == 201, response.text
        assert response.json()["imported_count"] == count
        print(f"\nTIMING {name}: {count} rows, {len(content.encode()) / 1024:.0f} KiB, {elapsed:.2f} s")
        assert elapsed < 30
    assert counts() == (2 * count, 2)
