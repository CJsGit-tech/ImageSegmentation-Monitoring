"""Batch 6: system administration (SCREEN-40 to 43; LB-333 to 336, LB-346 to 349)."""

import time
from datetime import datetime, timedelta, timezone

from playwright.sync_api import Page, expect

from conftest import backend_request, login, open_page

ADMIN_PAGES = ("admin-users", "admin-logs", "admin-database", "admin-realtime-history")


def recent_window(minutes: int = 10) -> dict:
    now = datetime.now(timezone.utc)
    return {"start": (now - timedelta(minutes=minutes)).isoformat(), "end": (now + timedelta(minutes=1)).isoformat()}


def user_id(api, username: str) -> int:
    [item] = api.call("GET", "/users", params={"q": username}).json()["items"]
    return item["id"]


def test_role_change_applies_on_the_next_request_and_self_change_is_refused(run):
    admin = run.api("setup_admin")
    viewer = run.accounts["viewer"]
    viewer_api = run.api("viewer")
    assert viewer_api.call("POST", "/records", json={"title": "x", "value": 1, "category": run.category("role")}).status_code == 403

    target = user_id(admin, viewer.username)
    assert admin.call("PATCH", f"/users/{target}/role", json={"role": "User"}).status_code == 200
    created = viewer_api.call("POST", "/records", json={"title": "x", "value": 1, "category": run.category("role")})
    assert created.status_code == 201
    assert admin.call("PATCH", f"/users/{target}/role", json={"role": "Viewer"}).status_code == 200

    me = user_id(admin, run.accounts["setup_admin"].username)
    refused = admin.call("PATCH", f"/users/{me}/role", json={"role": "Viewer"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "OWN_ROLE_CHANGE_FORBIDDEN"


def test_failed_login_is_saved_to_the_system_log(run):
    stranger = run.accounts["other"].username
    backend_request("POST", "/auth/login", json={"username": stranger, "password": "Wrong-Pass-1!"})
    admin = run.api("setup_admin")
    params = {**recent_window(), "event_type": "login_failure", "username": stranger}
    for _ in range(20):  # events reach the database about a second after they happen
        items = admin.call("GET", "/logs", params=params).json()["items"]
        if items:
            break
        time.sleep(0.5)
    assert items and items[0]["severity"] == "warning"


def test_database_status_and_history_are_admin_only(run):
    admin = run.api("setup_admin")
    status = admin.call("GET", "/db-status").json()
    assert status["status"] == "available" and status["refresh_interval_seconds"] == 10

    history = admin.call("GET", "/history/records", params=recent_window(5)).json()
    assert history["items"] and all(item["is_system"] for item in history["items"])

    user = run.api("user")
    for path in ("/users", "/logs/options", "/db-status"):
        assert user.call("GET", path).status_code == 403


def test_admin_screens_render_for_an_admin(page: Page, run):
    login(page, run.accounts["admin"])
    open_page(page, "admin-users")
    expect(page.get_by_text("第 1 /", exact=False).first).to_be_visible()
    expect(page.get_by_text("選取一列以變更角色")).to_be_visible()

    open_page(page, "admin-logs")
    expect(page.get_by_text("查詢範圍：", exact=False)).to_be_visible()

    open_page(page, "admin-database")
    expect(page.get_by_text("資料庫可用")).to_be_visible()
    expect(page.get_by_text("最後一次批次寫入時間")).to_be_visible()

    open_page(page, "admin-realtime-history")
    expect(page.get_by_text("此頁只顯示系統產生的記錄", exact=False)).to_be_visible()
    expect(page.locator("table").first).to_contain_text("系統")
    # PRD-16-R8: no export, so no data-grid toolbar with its CSV download on this page.
    expect(page.locator('[data-testid="stElementToolbar"]')).to_have_count(0)


def test_user_cannot_reach_admin_screens(page: Page, run):
    login(page, run.accounts["user"])
    expect(page.locator('[data-testid="stSidebar"]').get_by_text("系統管理")).to_have_count(0)
    for path in ADMIN_PAGES:
        open_page(page, path)
        expect(page.get_by_text("你的角色（User）不能使用此功能")).to_be_visible()
