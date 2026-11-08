"""Excel download checks (LB-332, PRD-12) against a real MariaDB. Run through tests/run_integration.sh.

Files are read back with openpyxl, a different library from the writer (XlsxWriter). Excel itself
is not available in the test environment.
"""

import asyncio
import glob
import io
import os
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import unquote

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.service import register_user
from app.core.config import load_settings
from app.db.migrate import upgrade_to_head
from app.db.session import create_unpooled_engine, get_session
from app.exports import service as export_service
from app.exports.excel import XLSX_MEDIA_TYPE
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
EXPORT = "/api/v1/analytics/export"
STATISTICS = "/api/v1/analytics/statistics"
ROLES = ("admin1", "user1", "viewer1")
TAIPEI = timezone(timedelta(hours=8))
DAY = datetime(2026, 3, 10)  # Naive UTC.
DETAIL_HEADERS = ["標題", "數值", "分類", "時間戳（UTC+08:00）", "創建者", "異常"]
SUMMARY_HEADERS = ["分類", "總計", "平均", "最大值", "最小值"]


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
    """rows: (title, category, value, naive UTC timestamp, is_anomaly, creator_id)."""

    async def work(session):
        session.add_all(
            Record(title=t, category=c, value=v, timestamp=ts, is_anomaly=a, created_by_user_id=u)
            for t, c, v, ts, a, u in rows
        )
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

    async def ids(session):
        return {user.username: user.id for user in await session.scalars(select(User))}

    Api.ids = run_in_db(ids)
    yield Api

    async def teardown(session):
        await _clear(session)
        await session.execute(delete(User))
        await session.commit()

    run_in_db(teardown)


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


def download(api, who="viewer1", **params):
    response = get(api, EXPORT, who, **params)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    return response


def workbook(response):
    return openpyxl.load_workbook(io.BytesIO(response.content))


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    error = response.json()["error"]
    assert error["code"] == code, response.text
    return error


def summary_rows(sheet):
    """(conditions dict, header, category rows) from the 摘要 sheet."""
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    assert rows[0][0] == "查詢條件"
    conditions = {row[0]: row[1] for row in rows[1:5]}
    assert all(value is None for value in rows[5])  # Blank spacer row.
    return conditions, rows[6][:5], [row[:5] for row in rows[7:]]


def detail_rows(sheet):
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    return rows[0], rows[1:]


def standard_seed(api):
    user = api.ids["user1"]
    seed([
        ("溫度 A", "溫度", 20.5, DAY + timedelta(hours=1, minutes=2, seconds=3), False, None),
        ("溫度 B", "溫度", 99.25, DAY + timedelta(hours=2), True, user),
        ("溫度 C", "溫度", -3.0, DAY + timedelta(hours=3, microseconds=250000), False, None),
        ("壓力 A", "壓力", 1.0 / 3.0, DAY + timedelta(hours=1, minutes=30), False, user),
        ("壓力 B", "壓力", 7.0, DAY + timedelta(hours=4), True, None),
        ("轉速 A", "Rpm", 1500.0, DAY + timedelta(hours=5), False, None),
        ("範圍外", "溫度", 1000.0, DAY + timedelta(days=2), False, None),  # Outside the range.
    ])


RANGE = {
    "start": datetime(2026, 3, 10, 8, 0, tzinfo=TAIPEI).isoformat(),  # 2026-03-10 00:00 UTC.
    "end": datetime(2026, 3, 11, 8, 0, tzinfo=TAIPEI).isoformat(),
}


def test_file_opens_with_two_sheets_and_its_name_carries_the_generation_time(client):
    standard_seed(client)
    response = download(client, **RANGE)
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="analysis_')
    assert "filename*=UTF-8''" in disposition
    name = unquote(disposition.split("filename*=UTF-8''")[1])
    assert name.startswith("資料分析_") and name.endswith(".xlsx")
    stamp = datetime.strptime(name.removeprefix("資料分析_").removesuffix(".xlsx"), "%Y%m%d-%H%M%S")
    now_local = datetime.now(TAIPEI).replace(tzinfo=None)  # Generation time is in start's offset.
    assert abs((now_local - stamp).total_seconds()) < 120
    assert response.headers["cache-control"] == "no-store"
    book = workbook(response)
    assert book.sheetnames == ["摘要", "明細"]
    conditions, _, _ = summary_rows(book["摘要"])
    assert conditions["產生時間"] == stamp.strftime("%Y-%m-%d %H:%M:%S") + " +08:00"


def test_summary_matches_statistics_with_one_row_per_category_and_no_total(client):
    standard_seed(client)
    statistics = get(client, STATISTICS, **RANGE).json()
    book = workbook(download(client, **RANGE))
    conditions, header, rows = summary_rows(book["摘要"])
    assert conditions["開始時間"] == "2026-03-10 08:00:00 +08:00"
    assert conditions["結束時間"] == "2026-03-11 08:00:00 +08:00"
    assert conditions["分類"] == "全部分類"
    assert conditions["產生時間"].endswith(" +08:00")
    assert header == SUMMARY_HEADERS
    assert [row[0] for row in rows] == [item["category"] for item in statistics["categories"]]
    for row, item in zip(rows, statistics["categories"], strict=True):
        # Excel keeps 15 significant digits; XlsxWriter writes 16.
        assert row[1:] == pytest.approx([item["sum"], item["avg"], item["max"], item["min"]], rel=1e-15)
    assert len(rows) == 3  # 溫度, 壓力, Rpm: no total or count row.
    assert not any(str(cell).startswith(("合計", "總計")) for row in rows for cell in row[:1])


def test_selected_categories_are_shown_and_one_without_data_gets_empty_statistics(client):
    standard_seed(client)
    params = {**RANGE, "category": ["壓力", "溫度", "濕度"]}
    statistics = get(client, STATISTICS, **params).json()
    book = workbook(download(client, **params))
    conditions, _, rows = summary_rows(book["摘要"])
    assert conditions["分類"] == "壓力、溫度、濕度"
    assert [row[0] for row in rows] == ["壓力", "溫度", "濕度"]
    assert rows[2][1:] == [None, None, None, None]
    assert [row[0] for row in rows] == [item["category"] for item in statistics["categories"]]
    _, details = detail_rows(book["明細"])
    assert len(details) == sum(item["count"] for item in statistics["categories"]) == 5
    assert {row[2] for row in details} == {"壓力", "溫度"}


def test_detail_lists_every_counted_record_oldest_first_with_anomalies_marked(client):
    standard_seed(client)
    statistics = get(client, STATISTICS, **RANGE).json()
    book = workbook(download(client, **RANGE))
    sheet = book["明細"]
    header, rows = detail_rows(sheet)
    assert header == DETAIL_HEADERS
    assert len(rows) == sum(item["count"] for item in statistics["categories"]) == 6
    assert [row[0] for row in rows] == ["溫度 A", "壓力 A", "溫度 B", "溫度 C", "壓力 B", "轉速 A"]
    first = rows[0]
    assert first[1] == 20.5 and first[2] == "溫度"
    assert first[3] == datetime(2026, 3, 10, 9, 2, 3)  # 01:02:03 UTC shown at +08:00, a real date cell.
    assert first[4] == "系統" and first[5] is None
    assert rows[1][4] == "user1"
    # Excel stores dates as day fractions: sub-second parts survive to about a millisecond.
    assert abs(rows[3][3] - datetime(2026, 3, 10, 11, 0, 0, 250000)) <= timedelta(milliseconds=1)
    marked = [row[0] for row in rows if row[5] == "異常"]
    assert marked == ["溫度 B", "壓力 B"]
    # The anomaly rows are also filled; the others are not.
    fills = {sheet.cell(row=index + 2, column=1).fill.fgColor.rgb for index in range(len(rows))}
    assert sheet.cell(row=4, column=1).fill.fill_type == "solid"
    assert sheet.cell(row=2, column=1).fill.fill_type is None
    assert len(fills) == 2
    assert sheet.cell(row=2, column=4).number_format == "yyyy-mm-dd hh:mm:ss"
    assert sheet.freeze_panes == "A2"


def test_file_has_no_chart_and_no_formula(client):
    standard_seed(client)
    response = download(client, **RANGE)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert not [name for name in names if "chart" in name or "drawing" in name]
        for name in names:
            if name.startswith("xl/worksheets/"):
                assert b"<f>" not in archive.read(name) and b"<f " not in archive.read(name)


def test_user_text_that_looks_like_a_formula_stays_text(client):
    hostile = ["=1+1", "+1", "-1", "@SUM(A1)", '=HYPERLINK("http://example.invalid","x")', "\t=1", "=cmd|' /C calc'!A0"]
    seed([(text, "=分類", float(index), DAY + timedelta(minutes=index), False, None) for index, text in enumerate(hostile)])
    seed([("一般", "-負號分類", 1.0, DAY + timedelta(hours=1), False, None)])
    book = workbook(download(client, **RANGE))
    _, rows = detail_rows(book["明細"])
    sheet = book["明細"]
    assert [row[0] for row in rows[: len(hostile)]] == hostile
    for index in range(len(hostile)):
        for column in (1, 3):  # Title and category.
            cell = sheet.cell(row=index + 2, column=column)
            assert cell.data_type == "s"
            assert cell.quotePrefix is True
    plain = sheet.cell(row=len(hostile) + 2, column=1)
    assert plain.value == "一般" and plain.quotePrefix is False
    summary = book["摘要"]
    _, _, summary_category_rows = summary_rows(summary)
    assert [row[0] for row in summary_category_rows] == ["-負號分類", "=分類"]
    for row in summary.iter_rows(min_row=8, max_col=1):
        assert row[0].data_type == "s" and row[0].quotePrefix is True


def test_condition_text_with_a_formula_like_category_stays_text(client):
    seed([("t", "=x", 1.0, DAY + timedelta(hours=1), False, None)])
    book = workbook(download(client, **RANGE, category=["=x"]))
    cell = book["摘要"]["B4"]
    assert cell.value == "=x" and cell.data_type == "s" and cell.quotePrefix is True


def test_empty_state_returns_no_file_and_explains(client):
    standard_seed(client)
    params = {"start": "2026-01-01T00:00:00+08:00", "end": "2026-01-02T00:00:00+08:00"}
    error = assert_error(get(client, EXPORT, **params), 404, "EXPORT_NO_DATA")
    assert "沒有可下載的內容" in error["message"]
    assert error["details"] == {**params, "category": None}
    # Selected categories without any data in range are empty too.
    assert_error(get(client, EXPORT, **RANGE, category=["濕度"]), 404, "EXPORT_NO_DATA")


@pytest.mark.parametrize("who", ROLES)
def test_every_role_can_download(client, who):
    standard_seed(client)
    download(client, who, **RANGE)


def test_unauthenticated_is_refused(client):
    standard_seed(client)
    assert_error(get(client, EXPORT, None, **RANGE), 401, "UNAUTHORIZED")


def test_reversed_range_is_refused(client):
    error = assert_error(get(client, EXPORT, start=RANGE["end"], end=RANGE["start"]), 422, "VALIDATION_ERROR")
    assert error["details"][0]["type"] == "range_reversed"


def test_bad_time_format_is_refused(client):
    assert_error(get(client, EXPORT, start="2026-03-10", end=RANGE["end"]), 422, "VALIDATION_ERROR")


def test_exactly_three_months_is_allowed_and_one_second_more_is_refused(client):
    seed([("t", "溫度", 1.0, datetime(2026, 2, 1), False, None)])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    download(client, start=start.isoformat(), end=datetime(2026, 4, 1, tzinfo=UTC).isoformat())
    error = assert_error(
        get(client, EXPORT, start=start.isoformat(), end=datetime(2026, 4, 1, 0, 0, 1, tzinfo=UTC).isoformat()),
        422,
        "RANGE_TOO_LONG",
    )
    assert error["details"]["max_months"] == 3


def test_more_detail_rows_than_the_cap_are_refused_before_writing(client, monkeypatch):
    standard_seed(client)
    monkeypatch.setattr(export_service, "MAX_DETAIL_ROWS", 5)
    error = assert_error(get(client, EXPORT, **RANGE), 422, "EXPORT_TOO_LARGE")
    assert error["details"] == {"rows": 6, "max_rows": 5}
    monkeypatch.setattr(export_service, "MAX_DETAIL_ROWS", 6)  # Exactly at the cap is allowed.
    download(client, **RANGE)


def test_detail_rows_span_several_batches_without_gaps(client, monkeypatch):
    monkeypatch.setattr(export_service, "BATCH_SIZE", 4)
    seed([(f"r{index:02d}", "溫度", float(index), DAY + timedelta(seconds=index // 2), False, None) for index in range(23)])
    _, rows = detail_rows(workbook(download(client, **RANGE))["明細"])
    assert sorted(row[0] for row in rows) == [f"r{index:02d}" for index in range(23)]
    assert len(rows) == 23


def test_temporary_files_are_removed(client):
    standard_seed(client)
    pattern = os.path.join(tempfile.gettempdir(), "analysis-export-*")
    before = set(glob.glob(pattern))
    download(client, **RANGE)
    get(client, EXPORT, start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z")  # Empty state.
    assert set(glob.glob(pattern)) == before


def test_times_use_the_offset_of_start(client):
    seed([("t", "溫度", 1.0, DAY + timedelta(hours=1), False, None)])
    params = {"start": "2026-03-09T19:00:00-05:00", "end": "2026-03-10T19:00:00-05:00"}
    book = workbook(download(client, **params))
    conditions, _, _ = summary_rows(book["摘要"])
    assert conditions["開始時間"] == "2026-03-09 19:00:00 -05:00"
    header, rows = detail_rows(book["明細"])
    assert header[3] == "時間戳（UTC-05:00）"
    assert rows[0][3] == datetime(2026, 3, 9, 20, 0)
