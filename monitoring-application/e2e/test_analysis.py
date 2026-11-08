"""Batch 5: statistics, trend, anomaly list and Excel export (SCREEN-30, LB-331, LB-332, LB-345)."""

import io
import zipfile
from datetime import datetime, timedelta, timezone

from playwright.sync_api import Page, expect

from conftest import button, choose, login, open_page

TAIPEI = timezone(timedelta(hours=8))


def sheet_text(content: bytes) -> str:
    """All cell text of the workbook, read from the raw XML (no spreadsheet library needed)."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [n for n in archive.namelist() if n.startswith("xl/worksheets/") or n == "xl/sharedStrings.xml"]
        return "".join(archive.read(n).decode() for n in names)


def window() -> dict:
    now = datetime.now(TAIPEI)
    return {"start": (now - timedelta(days=1)).isoformat(), "end": (now + timedelta(minutes=5)).isoformat()}


def test_statistics_are_per_category_with_no_total(run):
    category = run.category("stats")
    api = run.api("other")
    for value in (1, 2, 3, 10):
        api.create(f"統計 {value}", value, category)
    body = api.call("GET", "/analytics/statistics", params={**window(), "category": category}).json()
    [row] = body["categories"]
    assert (row["sum"], row["avg"], row["max"], row["min"], row["count"]) == (16, 4, 10, 1, 4)


def test_trend_refuses_too_many_points_and_the_list_holds_only_anomalies(run):
    category = run.category("trend")
    run.api("setup_admin").set_range(category, 0, 100)
    api = run.api("other")
    api.create("一般", 50, category)
    api.create("異常", 100, category, confirm_anomaly=True)

    now = datetime.now(TAIPEI)
    too_long = {"start": (now - timedelta(days=30)).isoformat(), "end": now.isoformat(), "granularity": "minute"}
    refused = api.call("GET", "/analytics/trend", params=too_long)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "TOO_MANY_POINTS"

    anomalies = api.call("GET", "/analytics/anomalies", params={**window(), "category": category}).json()
    assert [item["title"] for item in anomalies["items"]] == ["異常"]


def test_export_matches_the_query_and_refuses_an_empty_range(run):
    category = run.category("export")
    api = run.api("other")
    api.create("匯出", 7, category)
    response = api.call("GET", "/analytics/export", params={**window(), "category": category})
    assert response.status_code == 200
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    workbook = zipfile.ZipFile(io.BytesIO(response.content)).read("xl/workbook.xml").decode()
    assert "摘要" in workbook and "明細" in workbook
    assert not any(name.startswith("xl/charts") for name in zipfile.ZipFile(io.BytesIO(response.content)).namelist())

    empty = api.call("GET", "/analytics/export", params={"start": "2020-01-01T00:00:00+08:00", "end": "2020-01-02T00:00:00+08:00"})
    assert empty.status_code == 404 and empty.json()["error"]["code"] == "EXPORT_NO_DATA"


def test_viewer_queries_a_category_and_downloads_the_excel(page: Page, run):
    category = run.category("screen")
    run.api("setup_admin").set_range(category, None, None, unit="kPa")  # Unit only (PRD-17-R10).
    api = run.api("other")
    for value in (2, 4, 6):
        api.create(f"畫面 {value}", value, category)

    login(page, run.accounts["viewer"])
    open_page(page, "analysis")
    expect(page.get_by_text("設定條件後按「查詢」")).to_be_visible()
    expect(button(page, "analysis_download")).to_be_disabled()

    choose(page, "分類", category)
    page.keyboard.press("Escape")
    button(page, "analysis_query").click()

    # The statistics table is a data grid; its visible rows are mirrored in an accessible table.
    table = page.locator("table").first
    expect(table).to_contain_text(category)
    expect(table).to_contain_text("12")  # 2 + 4 + 6
    expect(table).not_to_contain_text("筆數")
    expect(button(page, "analysis_download")).to_be_enabled()

    button(page, "analysis_download").click()
    save = button(page, "analysis_save")
    expect(save).to_be_visible()
    with page.expect_download() as download_info:
        save.click()
    download = download_info.value
    assert download.suggested_filename.endswith(".xlsx")
    expect(page.get_by_text("已下載", exact=False).first).to_be_visible()
    # PRD-12-R7: both sheets carry a 單位 column with the category's unit.
    text = sheet_text(open(download.path(), "rb").read())
    assert text.count("單位") >= 2 and "kPa" in text


def test_changed_conditions_disable_download(page: Page, run):
    category = run.category("changed")
    run.api("other").create("條件", 1, category)
    login(page, run.accounts["user"])
    open_page(page, "analysis")
    choose(page, "分類", category)
    page.keyboard.press("Escape")
    button(page, "analysis_query").click()
    expect(button(page, "analysis_download")).to_be_enabled()

    page.get_by_text("每天", exact=True).click()
    expect(page.get_by_text("條件已變更", exact=False).first).to_be_visible()
    expect(button(page, "analysis_download")).to_be_disabled()
