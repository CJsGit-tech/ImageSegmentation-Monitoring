"""Batch 7: assignment requirement fixes.

- Test accounts for all three roles are created at start-up from .env.
- The simulator generates one reading per device every second by default.
- The live monitoring window is fed by the backend WebSocket and redraws every second.
- Bar (column) charts: anomaly counts per category on the monitoring and analysis pages.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests
from playwright.sync_api import Page, expect

from conftest import API_URL, APP_DIR, ENV_FILE, _compose, button, choose, login, open_page

TAIPEI = timezone(timedelta(hours=8))
ROLES = {"ADMIN": "Admin", "USER": "User", "VIEWER": "Viewer"}


def env_values() -> dict[str, str]:
    """KEY=VALUE pairs of the env file the stack was started with."""
    path = ENV_FILE or str(APP_DIR / ".env")
    values = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def live_chip(page: Page):
    return page.get_by_text(re.compile(r"已連線 · 最後資料 \d\d:\d\d:\d\d"))


def test_test_accounts_from_env_log_in_with_their_roles():
    env = env_values()
    for key, role in ROLES.items():
        username, password = env[f"INITIAL_{key}_USERNAME"], env[f"INITIAL_{key}_PASSWORD"]
        response = requests.post(f"{API_URL}/auth/login", json={"username": username, "password": password}, timeout=10)
        assert response.status_code == 200, f"{key}: {response.text}"
        assert response.json()["user"]["role"] == role


def test_simulator_generates_every_second_by_default(run):
    window = run.api("other").call("GET", "/realtime/window").json()
    expected = float(env_values().get("SIMULATOR_INTERVAL_SECONDS") or 1)
    assert window["interval_seconds"] == expected
    if expected == 1:
        readings = window["categories"][0]["readings"]
        stamps = [datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) for r in readings[-6:]]
        gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
        assert gaps and max(gaps) < 2.5, gaps


def _frontend_backend_sockets() -> int:
    """Established TCP connections from the frontend container to the backend's port 8000."""
    script = (
        "n=0\n"
        "for name in ('/proc/net/tcp', '/proc/net/tcp6'):\n"
        "    try:\n"
        "        rows = open(name).read().splitlines()[1:]\n"
        "    except OSError:\n"
        "        continue\n"
        "    for row in rows:\n"
        "        parts = row.split()\n"
        "        if parts[2].endswith(':1F40') and parts[3] == '01':\n"
        "            n += 1\n"
        "print(n)\n"
    )
    return int(_compose("exec", "-T", "frontend", "python", "-c", script).strip())


def test_live_window_is_pushed_over_a_websocket_and_redraws_every_second(page: Page, run):
    login(page, run.accounts["viewer"])
    open_page(page, "")
    chip = live_chip(page)
    expect(chip).to_be_visible()

    # A pushed reading every second: the chip's time changes several times within a few seconds,
    # which a 5-second poll could not do.
    seen = {chip.inner_text()}
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and len(seen) < 4:
        page.wait_for_timeout(400)
        seen.add(chip.inner_text())
    assert len(seen) >= 4, seen

    # The feed holds one long-lived connection to the backend instead of short polling requests.
    assert _frontend_backend_sockets() >= 1
    page.wait_for_timeout(2500)
    assert _frontend_backend_sockets() >= 1


def test_live_window_leaves_for_login_when_the_session_ends_elsewhere(page: Page, run):
    account = run.accounts["admin"]
    login(page, account)
    open_page(page, "")
    expect(live_chip(page)).to_be_visible()
    account.token()  # Logging in elsewhere supersedes the browser's session; the socket closes with 4401.
    expect(page).to_have_url(re.compile(r"/login"), timeout=15_000)


def test_monitoring_shows_the_anomaly_count_bar_chart(page: Page, run):
    login(page, run.accounts["user"])
    open_page(page, "")
    expect(live_chip(page)).to_be_visible()
    expect(page.get_by_text("異常次數（近 5 分鐘）柱狀圖")).to_be_visible()


def test_analysis_shows_anomaly_counts_per_category_as_a_bar_chart(page: Page, run):
    category = run.category("bars")
    run.api("setup_admin").set_range(category, 0, 100)
    api = run.api("other")
    api.create("一般", 50, category)
    api.create("異常 1", 150, category, confirm_anomaly=True)
    api.create("異常 2", 200, category, confirm_anomaly=True)

    now = datetime.now(TAIPEI)
    params = {"start": (now - timedelta(days=1)).isoformat(), "end": (now + timedelta(minutes=5)).isoformat(), "category": category}
    [row] = api.call("GET", "/analytics/statistics", params=params).json()["categories"]
    assert (row["count"], row["anomaly_count"]) == (3, 2)

    login(page, run.accounts["viewer"])
    open_page(page, "analysis")
    choose(page, "分類", category)
    page.keyboard.press("Escape")
    button(page, "analysis_query").click()
    expect(page.get_by_text("各分類異常次數（柱狀圖）")).to_be_visible()
