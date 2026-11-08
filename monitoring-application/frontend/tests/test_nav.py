from pathlib import Path
from types import SimpleNamespace

import pytest

from core import nav

FRONTEND = Path(__file__).resolve().parents[1]
ALL_PAGES = [page for pages in nav.GROUPS.values() for page in pages]


@pytest.fixture
def fake_page(monkeypatch):
    """st.Page needs a running app, so this keeps what it was given."""
    monkeypatch.setattr(nav.st, "Page", lambda file, **options: SimpleNamespace(file=file, **options))


def test_every_page_file_exists():
    missing = [page.file for page in ALL_PAGES if not (FRONTEND / page.file).is_file()]

    assert missing == []


def test_page_titles_and_urls_are_unique():
    assert len({page.title for page in ALL_PAGES}) == len(ALL_PAGES)
    assert len({page.url_path for page in ALL_PAGES}) == len(ALL_PAGES)


def test_every_admin_page_needs_system_admin():
    assert {page.capability for page in nav.GROUPS["系統管理"]} == {"system.admin"}


@pytest.mark.parametrize(
    ("title", "capability"),
    [("即時監控", "monitoring.view"), ("新增資料", "records.create"), ("使用者管理", "system.admin")],
)
def test_required_capability_is_looked_up_by_title(title, capability):
    assert nav.required_capability(SimpleNamespace(title=title)) == capability


@pytest.mark.parametrize(("url_path", "public"), [("login", True), ("register", True), ("records", False), ("", False)])
def test_only_login_and_register_are_public(url_path, public):
    assert nav.is_public(SimpleNamespace(url_path=url_path)) is public


def test_is_register_matches_only_the_register_page():
    assert nav.is_register(SimpleNamespace(url_path="register"))
    assert not nav.is_register(SimpleNamespace(url_path="login"))


def test_navigation_hides_pages_without_removing_them(fake_page):
    sections = nav.navigation_pages(frozenset({"records.read"}))

    registered = {page.url_path: page.visibility for group, pages in sections.items() if group for page in pages}
    assert len(registered) == len(ALL_PAGES)
    assert [url for url, visibility in registered.items() if visibility == "visible"] == ["records"]


def test_navigation_adds_the_public_pages_hidden(fake_page):
    public = nav.navigation_pages(frozenset())[""]

    assert [(page.url_path, page.visibility) for page in public] == [("login", "hidden"), ("register", "hidden")]


def test_only_the_home_page_is_the_default(fake_page):
    sections = nav.navigation_pages(frozenset())

    defaults = [page.url_path for group, pages in sections.items() if group for page in pages if page.default]
    assert defaults == [nav.HOME.url_path]
