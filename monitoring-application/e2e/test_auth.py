"""Batch 1: register, log in and out, stay logged in, and role-based pages (SCREEN-01 to 04)."""

from playwright.sync_api import Page, expect

from conftest import BASE_URL, PASSWORD, login, open_page


def test_register_then_log_in_as_viewer(page: Page, run):
    username = f"{run.prefix}-registered"
    open_page(page, "register")
    page.get_by_label("帳號").fill(username)
    page.get_by_label("密碼", exact=True).fill(PASSWORD)
    page.get_by_label("確認密碼").fill(PASSWORD)
    page.get_by_role("button", name="註冊").click()
    expect(page.get_by_text("註冊成功。新帳號為 Viewer（唯讀）", exact=False)).to_be_visible()

    page.get_by_role("button", name="前往登入").click()
    expect(page.get_by_label("帳號")).to_have_value(username)
    page.get_by_label("密碼", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="登入").click()

    sidebar = page.locator('[data-testid="stSidebar"]')
    expect(sidebar.get_by_text("資料列表")).to_be_visible()
    expect(sidebar.get_by_text("新增資料")).to_have_count(0)
    expect(sidebar.get_by_text("Viewer", exact=True)).to_be_visible()


def test_wrong_password_is_refused(page: Page, run):
    open_page(page, "login")
    page.get_by_label("帳號").fill(run.accounts["user"].username)
    page.get_by_label("密碼", exact=True).fill("Wrong-Pass-1!")
    page.get_by_role("button", name="登入").click()
    expect(page.get_by_text("帳號或密碼錯誤")).to_be_visible()


def test_login_survives_reload_and_logout_explains_itself(page: Page, run):
    login(page, run.accounts["user"])
    page.reload()
    expect(page.get_by_text(run.accounts["user"].username, exact=True)).to_be_visible()

    page.get_by_role("button", name="登出").click()
    expect(page).to_have_url(f"{BASE_URL}/login")
    expect(page.get_by_text("你已登出。")).to_be_visible()


def test_admin_sees_system_pages(page: Page, run):
    login(page, run.accounts["admin"])
    sidebar = page.locator('[data-testid="stSidebar"]')
    expect(sidebar.get_by_text("系統管理")).to_be_visible()
    expect(sidebar.get_by_text("異常範圍設定")).to_be_visible()


def test_viewer_cannot_reach_add_or_range_pages_by_url(page: Page, run):
    login(page, run.accounts["viewer"])
    for path in ("records-new", "admin-anomaly-ranges"):
        open_page(page, path)
        expect(page.get_by_text("你的角色（Viewer）不能使用此功能")).to_be_visible()
