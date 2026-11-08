"""Batch 3: bulk CSV and JSON import (SCREEN-14, LB-328, LB-343)."""

import json

from playwright.sync_api import Page, expect

from conftest import button, login, open_page

UPLOADER = '[data-testid="stFileUploaderDropzoneInput"]'


def upload(page: Page, name: str, content: str, mime: str = "text/csv") -> None:
    """Sets the file on Streamlit's hidden input, as choosing it in the file dialog would."""
    page.locator(UPLOADER).set_input_files({"name": name, "mimeType": mime, "buffer": content.encode()})
    # Choosing a file clears the last result once Streamlit has received it. Long names are
    # shortened in the file chip, so wait for that instead of the name.
    expect(page.locator('[data-testid="stMain"] [data-testid="stAlert"]')).to_have_count(0)
    expect(page.locator('[data-testid="stFileChip"]')).to_have_count(1)
    expect(page.locator('[data-testid="stFileChipIconSpinner"]')).to_have_count(0)
    submit = button(page, "records_import_submit")
    expect(submit).to_be_enabled()
    submit.click()


def csv(category: str, *rows: str) -> str:
    return "title,value,category,timestamp\n" + "".join(f"{row.format(c=category)}\n" for row in rows)


def open_import(page: Page, run) -> None:
    login(page, run.accounts["user"])
    open_page(page, "records-import")
    expect(button(page, "records_import_submit")).to_be_disabled()
    expect(page.get_by_text("請先選擇 .csv 或 .json 檔案")).to_be_visible()


def test_csv_import_succeeds_with_the_importer_as_creator(page: Page, run):
    category = run.category("imp")
    open_import(page, run)
    upload(page, f"{run.prefix}-ok.csv", csv(category, "甲,1.5,{c},", "乙,2,{c},2026-09-01T00:00:00+08:00"))
    expect(page.get_by_text("已導入 2 筆。")).to_be_visible()
    expect(page.get_by_role("link", name="前往資料列表")).to_be_visible()
    rows = run.api("setup_admin").records(category)
    assert sorted(row["title"] for row in rows) == ["乙", "甲"]
    assert {row["creator_name"] for row in rows} == {run.accounts["user"].username}


def test_json_import_marks_out_of_range_rows_without_asking(page: Page, run):
    category = run.category("impjson")
    run.api("setup_admin").set_range(category, 0, 100)
    open_import(page, run)
    body = json.dumps([{"title": "在範圍內", "value": 50, "category": category}, {"title": "邊界", "value": 100, "category": category}])
    upload(page, f"{run.prefix}-ok.json", body, "application/json")
    expect(page.get_by_text("已導入 2 筆。")).to_be_visible()
    flags = {row["title"]: row["is_anomaly"] for row in run.api("setup_admin").records(category)}
    assert flags == {"在範圍內": False, "邊界": True}


def test_bad_rows_refuse_the_whole_file_and_a_fixed_file_keeps_its_name(page: Page, run):
    category = run.category("impfix")
    name = f"{run.prefix}-fix.csv"
    open_import(page, run)
    upload(page, name, csv(category, "好,1,{c},", "壞,abc,{c},", "無時區,3,{c},2026-09-01T00:00:00"))
    expect(page.get_by_text("這個檔案的資料一筆都沒有寫入", exact=False)).to_be_visible()
    expect(page.get_by_text("共 2 個問題", exact=False)).to_be_visible()
    assert run.api("setup_admin").records(category) == []

    upload(page, name, csv(category, "好,1,{c},", "改好,2,{c},", "有時區,3,{c},2026-09-01T00:00:00+08:00"))
    expect(page.get_by_text("已導入 3 筆。")).to_be_visible()
    assert len(run.api("setup_admin").records(category)) == 3


def test_same_name_is_refused_and_same_content_is_explained(page: Page, run):
    category = run.category("impdup")
    name = f"{run.prefix}-dup.csv"
    content = csv(category, "一,1,{c},")
    open_import(page, run)
    upload(page, name, content)
    expect(page.get_by_text("已導入 1 筆。")).to_be_visible()

    upload(page, name.upper(), content)
    expect(page.get_by_text("相同檔名已成功導入過", exact=False)).to_be_visible()
    expect(page.get_by_text("資料已經寫入，不需要再上傳", exact=False)).to_be_visible()
    assert len(run.api("setup_admin").records(category)) == 1


def test_unsupported_and_unparseable_files_are_explained(page: Page, run):
    open_import(page, run)
    upload(page, f"{run.prefix}-notes.txt", "hello", "text/plain")
    expect(page.get_by_text("只接受 .csv 或 .json 檔案", exact=False)).to_be_visible()

    upload(page, f"{run.prefix}-broken.json", '[{"title": ', "application/json")
    expect(page.get_by_text("檔案無法解析", exact=False)).to_be_visible()

    upload(page, f"{run.prefix}-unit.csv", "title,value,category,unit\nx,1,c,kPa\n")
    expect(page.get_by_text("欄位：unit", exact=False)).to_be_visible()


def test_viewer_cannot_reach_import(page: Page, run):
    login(page, run.accounts["viewer"])
    expect(page.locator('[data-testid="stSidebar"]').get_by_text("批量導入")).to_have_count(0)
    open_page(page, "records-import")
    expect(page.get_by_text("你的角色（Viewer）不能使用此功能")).to_be_visible()
    expect(page.locator(UPLOADER)).to_have_count(0)
