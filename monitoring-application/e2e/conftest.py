"""Browser tests against a running stack. See e2e/README.md before running them.

Each run registers its own accounts and uses its own categories, all named with a unique
"e2e-<id>" prefix, and removes them at the end. Roles are set through db_tool.py inside the
backend container, because the app has no role API yet. The backend is not published to the host,
so API calls also run inside that container, through backend_request.py.
"""

import base64
import json as jsonlib
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:18501").rstrip("/")
ENV_FILE = os.environ.get("E2E_ENV_FILE")
KEEP_DATA = os.environ.get("E2E_KEEP_DATA") == "1"
APP_DIR = Path(__file__).resolve().parent.parent
DB_TOOL = Path(__file__).resolve().parent / "db_tool.py"
BACKEND_REQUEST = Path(__file__).resolve().parent / "backend_request.py"

# Throwaway accounts, created and removed by every run.
PASSWORD = "E2e-Run-Pass-7!"
# Streamlit reruns the page after most actions, so allow time for the redraw.
expect.set_options(timeout=20_000)


def _compose(*args: str, stdin: bytes | None = None) -> str:
    command = ["docker", "compose"]
    if ENV_FILE:
        command += ["--env-file", ENV_FILE]
    result = subprocess.run(
        [*command, *args], cwd=APP_DIR, input=stdin, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed:\n{result.stderr.decode()}")
    return result.stdout.decode()


def db_tool(*args: str) -> str:
    return _compose("exec", "-T", "backend", "python", "-", *args, stdin=DB_TOOL.read_bytes())


@dataclass(frozen=True)
class BackendResponse:
    status_code: int
    headers: dict[str, str]  # Names are lower case.
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode(errors="replace")

    def json(self) -> Any:
        return jsonlib.loads(self.content)


def backend_request(
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> BackendResponse:
    """Calls /api/v1<path> from inside the backend container. A refusal is returned, not raised."""
    request = jsonlib.dumps({"method": method, "path": path, "json": json, "params": params, "headers": headers or {}})
    output = _compose("exec", "-T", "backend", "python", "-", request, stdin=BACKEND_REQUEST.read_bytes())
    response = jsonlib.loads(output)
    return BackendResponse(response["status"], response["headers"], base64.b64decode(response["body"]))


@dataclass
class Account:
    username: str
    role: str

    def token(self) -> str:
        """Logging in ends any other session of this account, including one in the browser."""
        response = backend_request("POST", "/auth/login", json={"username": self.username, "password": PASSWORD})
        assert response.status_code == 200, f"logging in as {self.username}: {response.text}"
        return response.json()["access_token"]


class Api:
    """Sets up data directly. Uses only accounts the browser never logs in as."""

    def __init__(self, account: Account):
        self.headers = {"Authorization": f"Bearer {account.token()}"}

    def call(self, method: str, path: str, **kwargs) -> BackendResponse:
        return backend_request(method, path, headers=self.headers, **kwargs)

    def create(self, title: str, value: float, category: str, **fields) -> dict:
        response = self.call("POST", "/records", json={"title": title, "value": value, "category": category, **fields})
        assert response.status_code == 201, response.text
        return response.json()

    def records(self, category: str) -> list[dict]:
        response = self.call("GET", "/records", params={"category": category, "limit": 100})
        assert response.status_code == 200, response.text
        return response.json()["items"]

    def set_range(self, category: str, low: float | None, high: float | None, unit: str | None = None) -> None:
        body = {"category": category, "range": None if low is None else {"min": low, "max": high}}
        if unit is not None:
            body["unit"] = unit
        response = self.call("PATCH", "/category-settings", json=body)
        assert response.status_code == 200, response.text


@dataclass
class Run:
    prefix: str
    accounts: dict[str, Account]
    _counter: int = 0

    def category(self, name: str) -> str:
        """Starts with "!" so the run's categories list before older test data in every table."""
        self._counter += 1
        return f"!{self.prefix}-{name}{self._counter}"

    def api(self, who: str) -> Api:
        return Api(self.accounts[who])


@pytest.fixture(scope="session")
def run():
    prefix = f"e2e-{uuid.uuid4().hex[:8]}"
    roles = {"user": "User", "other": "User", "admin": "Admin", "setup_admin": "Admin", "viewer": "Viewer"}
    accounts = {}
    try:
        for who, role in roles.items():
            account = Account(f"{prefix}-{who}", role)
            response = backend_request("POST", "/auth/register", json={"username": account.username, "password": PASSWORD})
            assert response.status_code == 201, f"registering {account.username}: {response.text}"
            if role != "Viewer":
                db_tool("promote", account.username, role)
            accounts[who] = account
        yield Run(prefix, accounts)
    finally:
        if not KEEP_DATA:
            print(db_tool("cleanup", prefix, f"!{prefix}"))


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Uses the installed Google Chrome, so no Playwright browser download is needed."""
    channel = os.environ.get("E2E_BROWSER_CHANNEL", "chrome")
    return {**browser_type_launch_args, **({"channel": channel} if channel else {})}


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1280, "height": 900}, "locale": "zh-TW"}


def login(page: Page, account: Account) -> None:
    page.goto(f"{BASE_URL}/login")
    page.get_by_label("帳號").fill(account.username)
    page.get_by_label("密碼", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="登入").click()
    expect(page).not_to_have_url(re.compile(r"/login"))
    expect(page.get_by_text(account.username, exact=True)).to_be_visible()
    # The login cookie is set by a separate request just after: a page load before it lands
    # would be logged out.
    for _ in range(100):
        if any(cookie["name"] == "md_session" for cookie in page.context.cookies()):
            return
        page.wait_for_timeout(100)
    raise AssertionError("the login cookie was never set")


def open_page(page: Page, path: str) -> None:
    page.goto(f"{BASE_URL}/{path}")
    expect(page.locator('[data-testid="stApp"]')).to_be_visible()


def button(page: Page, key: str):
    """Streamlit tags each widget with its key, which survives copy changes."""
    return page.locator(f".st-key-{key} button")


def choose(page: Page, label: str, text: str, scope=None) -> None:
    """Selects or adds a selectbox option by typing it, as a person would."""
    box = (scope or page).get_by_label(label)
    box.click()
    box.fill(text)
    box.press("Enter")


def select_row(page: Page, index: int, table_index: int = 0) -> None:
    """Ticks a row's checkbox in an st.dataframe, which draws on a canvas.

    Rows are 35 px with a 35 px header, and the checkbox sits in the first 30 px.
    """
    grid = page.locator('[data-testid="stDataFrame"]').nth(table_index)
    expect(grid).to_be_visible()
    grid.scroll_into_view_if_needed()
    box = grid.bounding_box()
    assert box is not None
    page.mouse.click(box["x"] + 15, box["y"] + 35 + 35 * index + 17)
