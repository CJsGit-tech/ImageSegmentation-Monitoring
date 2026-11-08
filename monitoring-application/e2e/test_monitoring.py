"""Batch 4: simulated data and the real-time monitoring screen (SCREEN-20, LB-330, LB-344).

The simulator runs on its own schedule (30 s by default), so these tests read what it has produced
rather than creating data. Charts are drawn by Vega, so the tests check the text around them.
"""

import re
from datetime import datetime, timedelta, timezone

import requests
from playwright.sync_api import Page, expect

from conftest import API_URL, login, open_page

DEVICES = ("溫度", "壓力", "馬達轉速", "振動")


def live_chip(page: Page):
    return page.get_by_text(re.compile(r"已連線 · 最後資料 \d\d:\d\d:\d\d"))


def test_live_window_api_serves_four_devices_and_refuses_strangers(run):
    assert requests.get(f"{API_URL}/realtime/window", timeout=10).status_code == 401
    window = run.api("other").call("GET", "/realtime/window").json()
    assert [category["category"] for category in window["categories"]] == list(DEVICES)
    assert window["generator"]["running"] is True
    assert window["summary"]["last_reading_at"] is not None


def test_custom_range_api_caps_at_three_months(run):
    api = run.api("other")
    start = datetime(2026, 8, 7, tzinfo=timezone(timedelta(hours=8)))
    exactly = api.call("GET", "/realtime/range", params={"start": start.isoformat(), "end": "2026-11-07T00:00:00+08:00"})
    assert exactly.status_code == 200, exactly.text
    over = api.call("GET", "/realtime/range", params={"start": start.isoformat(), "end": "2026-11-07T00:00:01+08:00"})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "RANGE_TOO_LONG"


def test_viewer_sees_the_live_window_update_without_a_reload(page: Page, run):
    login(page, run.accounts["viewer"])
    open_page(page, "")
    for device in DEVICES:
        expect(page.get_by_text(device, exact=True).first).to_be_visible()
    expect(page.get_by_text("最近 5 分鐘異常筆數")).to_be_visible()
    chip = live_chip(page)
    expect(chip).to_be_visible()
    first = chip.inner_text()

    interval = run.api("other").call("GET", "/realtime/window").json()["interval_seconds"]
    # The chip shows the latest reading's time, so it changes on the next tick.
    expect(chip).not_to_have_text(first, timeout=int((interval + 15) * 1000))


def test_custom_range_shows_saved_data_and_switches_back_to_live(page: Page, run):
    login(page, run.accounts["user"])
    open_page(page, "")
    expect(live_chip(page)).to_be_visible()
    # The view toggle is a segmented control, whose options are not exposed as named buttons.
    page.get_by_text("自訂時間範圍", exact=True).click()
    expect(page.get_by_text("尚未查詢", exact=False).first).to_be_visible()

    # The form starts with the past hour, which always holds simulated readings.
    page.get_by_role("button", name="查詢", exact=True).click()
    expect(page.get_by_text("檢視已保存資料：", exact=False)).to_be_visible()
    expect(page.get_by_text("檢視已保存資料（不自動更新）")).to_be_visible()
    for device in DEVICES:
        expect(page.get_by_text(device, exact=True).first).to_be_visible()

    page.get_by_text("即時窗口", exact=True).click()
    expect(live_chip(page)).to_be_visible()
