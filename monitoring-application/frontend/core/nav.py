"""Page registry: every page, its navigation group, its URL and the capability it needs."""

from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True)
class PageDef:
    file: str
    title: str
    url_path: str
    capability: str


GROUPS: dict[str, tuple[PageDef, ...]] = {
    "監控": (PageDef("screens/monitoring.py", "即時監控", "monitoring", "monitoring.view"),),
    "資料": (
        PageDef("screens/records.py", "資料列表", "records", "records.read"),
        PageDef("screens/record_new.py", "新增資料", "records-new", "records.create"),
        PageDef("screens/records_import.py", "批量導入", "records-import", "records.import"),
    ),
    "分析": (PageDef("screens/analysis.py", "資料分析", "analysis", "analysis.view"),),
    "系統管理": (
        PageDef("screens/admin_users.py", "使用者管理", "admin-users", "system.admin"),
        PageDef("screens/admin_logs.py", "系統日誌", "admin-logs", "system.admin"),
        PageDef("screens/admin_database.py", "資料庫狀態", "admin-database", "system.admin"),
        PageDef("screens/admin_realtime_history.py", "即時資料歷史", "admin-realtime-history", "system.admin"),
        PageDef("screens/admin_anomaly_ranges.py", "異常範圍設定", "admin-anomaly-ranges", "system.admin"),
    ),
}
HOME = GROUPS["監控"][0]
_PUBLIC_FILES = {"login": "screens/login.py", "register": "screens/register.py"}
_CAPABILITY_BY_TITLE = {page.title: page.capability for pages in GROUPS.values() for page in pages}


def login_page() -> st.Page:
    return st.Page(_PUBLIC_FILES["login"], title="登入", url_path="login", visibility="hidden")


def register_page() -> st.Page:
    return st.Page(_PUBLIC_FILES["register"], title="註冊", url_path="register", visibility="hidden")


def home_page() -> st.Page:
    return _to_page(HOME, visible=True)


def _to_page(page: PageDef, *, visible: bool) -> st.Page:
    return st.Page(
        page.file,
        title=page.title,
        url_path=page.url_path,
        default=page is HOME,
        visibility="visible" if visible else "hidden",
    )


def navigation_pages(capabilities: frozenset[str]) -> dict[str, list[st.Page]]:
    """All pages stay registered, so a hidden page's URL reaches the guard instead of a 404."""
    sections = {
        group: [_to_page(page, visible=page.capability in capabilities) for page in pages]
        for group, pages in GROUPS.items()
    }
    sections[""] = [login_page(), register_page()]
    return sections


def is_register(page: st.Page) -> bool:
    return page.url_path == "register"


def is_public(page: st.Page) -> bool:
    return page.url_path in _PUBLIC_FILES


def required_capability(page: st.Page) -> str:
    return _CAPABILITY_BY_TITLE[page.title]
