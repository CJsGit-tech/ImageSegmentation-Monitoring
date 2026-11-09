"""Batch 2: add, list, edit, delete, anomaly ranges and warnings (SCREEN-10 to 13, SCREEN-44).

Every test sets up its own category through the API before the browser logs in, because an API
login would end the browser's session on the same account.
"""

import uuid

from playwright.sync_api import Page, expect

from conftest import button, choose, login, open_page, select_row

WARNING = "儲存後將標為異常"
SAVED_ANOMALY = "這筆記錄已標為異常。"
NOT_OWNER = "只有建立者或 Admin 可修改"
# The records screen shortens filter chip values to this many characters (_CHIP_TEXT_LENGTH).
CHIP_TEXT_LENGTH = 24


def category_chip(page: Page, category: str):
    return page.get_by_text(f"分類：{category[:CHIP_TEXT_LENGTH]}", exact=False).first


def fill_new_record(page: Page, title: str, value: str, category: str) -> None:
    open_page(page, "records-new")
    expect(page.get_by_label("標題")).to_be_editable()
    page.get_by_label("標題").fill(title)
    page.get_by_label("數值").fill(value)
    choose(page, "分類", category)


def filter_list(page: Page, category: str, *, sort_by_value: bool = True) -> None:
    open_page(page, "records")
    # Wait for the first load: a filter typed during it is lost when the form redraws.
    expect(page.locator('[data-testid="stDataFrame"]').first).to_be_visible()
    choose(page, "分類", category)
    if sort_by_value:
        choose(page, "排序欄位", "數值")
        choose(page, "排序方向", "遞增")
    button(page, "records_query").click()
    expect(category_chip(page, category)).to_be_visible()


def selected(page: Page, title: str):
    return expect(page.get_by_text(f"已選取：{title}", exact=False))


def test_add_without_range_saves_a_normal_record(page: Page, run):
    category = run.category("norange")
    login(page, run.accounts["user"])
    fill_new_record(page, "無範圍", "30", category)
    expect(page.get_by_text(WARNING)).to_have_count(0)
    page.get_by_role("button", name="送出").click()

    expect(page.get_by_text("已新增 1 筆記錄。")).to_be_visible()
    expect(page.get_by_text(f"分類：{category}")).to_be_visible()
    [record] = run.api("setup_admin").records(category)
    assert record["is_anomaly"] is False
    assert record["creator_name"] == run.accounts["user"].username


def test_value_at_a_bound_needs_confirmation_and_a_changed_value_drops_it(page: Page, run):
    category = run.category("warn")
    run.api("setup_admin").set_range(category, 20, 80, unit="°C")
    login(page, run.accounts["user"])
    fill_new_record(page, "邊界", "80", category)

    submit = page.get_by_role("button", name="送出")
    expect(page.get_by_text(WARNING)).to_be_visible()
    expect(submit).to_be_disabled()
    page.get_by_text("我了解，確認儲存為異常記錄").click()
    expect(submit).to_be_enabled()

    page.get_by_label("數值").fill("50")
    expect(page.get_by_text(WARNING)).to_have_count(0)
    submit.click()
    expect(page.get_by_text("已新增 1 筆記錄。")).to_be_visible()
    [record] = run.api("setup_admin").records(category)
    assert (record["value"], record["is_anomaly"]) == (50, False)


def test_confirmed_anomaly_is_saved_and_stays_editable(page: Page, run):
    category = run.category("lock")
    run.api("setup_admin").set_range(category, 20, 80)
    login(page, run.accounts["user"])
    fill_new_record(page, "超溫", "20", category)
    page.get_by_text("我了解，確認儲存為異常記錄").click()
    page.get_by_role("button", name="送出").click()
    expect(page.get_by_text("已新增 1 筆記錄。")).to_be_visible()

    filter_list(page, category)
    expect(page.get_by_text("◆ 異常", exact=False)).to_have_count(0)  # drawn on the canvas, not in the DOM
    select_row(page, 0)
    selected(page, "超溫").to_be_visible()
    expect(button(page, "records_edit")).to_be_enabled()
    expect(button(page, "records_delete")).to_be_enabled()
    assert run.api("setup_admin").records(category)[0]["is_anomaly"] is True


def test_list_actions_follow_role_owner_and_anomaly(page: Page, run):
    category = run.category("roles")
    setup = run.api("setup_admin")
    setup.set_range(category, 0, 100)
    run.api("other").create("他人的記錄", 50, category)
    user_api = run.api("user")
    user_api.create("自己的記錄", 10, category)
    user_api.create("自己的異常", 100, category, confirm_anomaly=True)

    login(page, run.accounts["user"])
    filter_list(page, category)
    # Sorted by value: 10 own, 50 other's, 100 own anomaly.
    select_row(page, 0)
    selected(page, "自己的記錄").to_be_visible()
    expect(button(page, "records_edit")).to_be_enabled()
    select_row(page, 1)
    selected(page, "他人的記錄").to_be_visible()
    expect(page.get_by_text(NOT_OWNER)).to_be_visible()
    expect(button(page, "records_edit")).to_be_disabled()
    select_row(page, 2)
    selected(page, "自己的異常").to_be_visible()
    expect(button(page, "records_edit")).to_be_enabled()
    expect(button(page, "records_delete")).to_be_enabled()


def test_admin_edits_others_records_including_anomalies(page: Page, run):
    category = run.category("admin")
    run.api("setup_admin").set_range(category, 0, 100)
    other = run.api("other")
    other.create("他人的一般記錄", 50, category)
    other.create("他人的異常", 0, category, confirm_anomaly=True)

    login(page, run.accounts["admin"])
    filter_list(page, category)
    select_row(page, 0)
    selected(page, "他人的異常").to_be_visible()
    expect(button(page, "records_edit")).to_be_enabled()
    expect(button(page, "records_delete")).to_be_enabled()
    select_row(page, 1)
    selected(page, "他人的一般記錄").to_be_visible()
    expect(button(page, "records_edit")).to_be_enabled()
    expect(button(page, "records_delete")).to_be_enabled()


def test_viewer_list_has_no_actions(page: Page, run):
    category = run.category("viewer")
    run.api("other").create("可讀的記錄", 1, category)
    login(page, run.accounts["viewer"])
    filter_list(page, category, sort_by_value=False)
    # A rerun can briefly hold the old table beside the new one.
    expect(page.locator('[data-testid="stDataFrame"]').first).to_be_visible()
    expect(button(page, "records_edit")).to_have_count(0)
    expect(page.get_by_role("link", name="新增資料")).to_have_count(0)


def test_edit_keeps_the_filter_and_delete_removes_the_record(page: Page, run):
    category = run.category("edit")
    run.api("user").create("修改前", 5, category)
    login(page, run.accounts["user"])
    filter_list(page, category)

    select_row(page, 0)
    button(page, "records_edit").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_text("修改記錄")).to_be_visible()
    dialog.get_by_label("標題").fill("修改後")
    button(page, "record_edit_save").click()
    expect(page.get_by_text("已儲存修改。")).to_be_visible()
    selected(page, "修改後").to_be_visible()
    expect(category_chip(page, category)).to_be_visible()

    button(page, "records_delete").click()
    expect(page.get_by_role("dialog").get_by_text("永久刪除這筆記錄？")).to_be_visible()
    button(page, "record_delete_cancel").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert len(run.api("setup_admin").records(category)) == 1

    button(page, "records_delete").click()
    button(page, "record_delete_confirm").click()
    expect(page.get_by_text("已刪除")).to_be_visible()
    assert run.api("setup_admin").records(category) == []


def test_edit_into_anomaly_needs_confirmation(page: Page, run):
    category = run.category("editwarn")
    run.api("setup_admin").set_range(category, 0, 100)
    run.api("user").create("將超出", 50, category)
    login(page, run.accounts["user"])
    filter_list(page, category)
    select_row(page, 0)
    button(page, "records_edit").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_label("數值").fill("100")
    expect(dialog.get_by_text(WARNING)).to_be_visible()
    expect(button(page, "record_edit_save")).to_be_disabled()
    dialog.get_by_text("我了解，確認儲存為異常記錄").click()
    button(page, "record_edit_save").click()
    expect(page.get_by_text(SAVED_ANOMALY)).to_be_visible()
    assert run.api("setup_admin").records(category)[0]["is_anomaly"] is True


def test_anomaly_can_be_corrected_then_deleted(page: Page, run):
    category = run.category("fix")
    run.api("setup_admin").set_range(category, 0, 100)
    run.api("other").create("填錯的值", 100, category, confirm_anomaly=True)
    login(page, run.accounts["admin"])
    filter_list(page, category)
    select_row(page, 0)
    selected(page, "填錯的值").to_be_visible()

    button(page, "records_edit").click()
    page.get_by_role("dialog").get_by_label("數值").fill("50")
    button(page, "record_edit_save").click()
    expect(page.get_by_text("已儲存修改。")).to_be_visible()
    assert run.api("setup_admin").records(category)[0]["is_anomaly"] is False

    button(page, "records_delete").click()
    button(page, "record_delete_confirm").click()
    expect(page.get_by_text("已刪除")).to_be_visible()
    assert run.api("setup_admin").records(category) == []


def test_admin_sets_and_clears_a_range(page: Page, run):
    category = run.category("settings")
    setup = run.api("setup_admin")
    run.api("other").create("有資料", 1, category)
    items = setup.call("GET", "/category-settings").json()["items"]
    index = next(i for i, item in enumerate(items) if item["category"] == category)
    assert index < 10, "the category must be within the table's first 10 visible rows"

    login(page, run.accounts["admin"])
    open_page(page, "admin-anomaly-ranges")
    select_row(page, index)
    selected(page, category).to_be_visible()
    button(page, "anomaly_ranges_edit").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_label("最小值").fill("80")
    dialog.get_by_label("最大值").fill("75")
    button(page, "anomaly_ranges_save").click()
    expect(dialog.get_by_text("最小值必須小於最大值")).to_be_visible()

    dialog.get_by_label("最大值").fill("90")
    dialog.get_by_label("單位（選填）").fill("kPa")
    button(page, "anomaly_ranges_save").click()
    expect(page.get_by_text(f"已儲存「{category}」的範圍", exact=False)).to_be_visible()
    display = {item["category"]: item for item in setup.call("GET", "/category-display").json()["items"]}
    assert display[category]["range"] == {"min": 80, "max": 90}

    button(page, "anomaly_ranges_clear").click()
    expect(page.get_by_role("dialog").get_by_text("之後的新資料不會再檢查是否異常", exact=False)).to_be_visible()
    button(page, "confirm_dialog_confirm").click()
    expect(page.get_by_text(f"已清除「{category}」的範圍", exact=False)).to_be_visible()
    display = {item["category"]: item for item in setup.call("GET", "/category-display").json()["items"]}
    assert display[category] == {"category": category, "range": None, "unit": "kPa"}
    assert run.api("other").create("清除後", 999, category)["is_anomaly"] is False


def test_retrying_a_save_with_the_same_key_adds_one_record(run):
    """The browser retry is covered by hand (it needs a 30 s stall); this checks the API side."""
    category = run.category("retry")
    api = run.api("other")
    body = {"title": "重送", "value": 1, "category": category, "client_request_id": str(uuid.uuid4())}
    first = api.call("POST", "/records", json=body)
    second = api.call("POST", "/records", json=body)
    assert first.status_code == second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert len(run.api("setup_admin").records(category)) == 1
