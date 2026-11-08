"""WebSocket client for the live monitoring window (SCREEN-20).

Each logged-in browser session that shows the live window owns one LiveFeed. A background thread
in the frontend container holds a WebSocket to the backend's /api/v1/realtime/ws, authenticates
with the session's token, and keeps a LiveSnapshot-shaped copy of the window up to date from the
pushed messages:

- `snapshot`: replaces the copy (sent once after every (re)connect);
- `readings`: appends each device's new reading, updates its latest value, range, unit and
  5-minute anomaly count, the summary, and the recent anomaly list, then drops points older than
  the window;
- `heartbeat`: refreshes the server time and generator state, so a stopped simulator shows as
  stalled.

The page redraws from that copy every second; it never polls the backend. Reconnects back off
1, 2, 4, 8 and then every 15 seconds. Close code 4401 (login lost) and 4403 (role) stop the feed
for good. A feed nobody has read for IDLE_STOP_SECONDS (page left, tab closed, logged out) closes
itself, so no socket outlives its viewer.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import ClientConnection, connect

WS_PATH = "/api/v1/realtime/ws"
OPEN_TIMEOUT_SECONDS = 5.0
# The server sends a heartbeat every 10 s; this long without any message means the link is dead.
SILENCE_SECONDS = 25.0
IDLE_STOP_SECONDS = 30.0
BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)
BACKOFF_CAP_SECONDS = 15.0
RECENT_ANOMALY_LIMIT = 20
AUTH_LOST_CODE = 4401
FORBIDDEN_CODE = 4403


def ws_url() -> str:
    base = os.environ["BACKEND_URL"].rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base + WS_PATH


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass(frozen=True)
class FeedView:
    """What the page draws from: a private copy, safe to read while the thread keeps updating."""

    snapshot: dict[str, Any] | None
    connected: bool
    failures: int
    # Set when the server closed for good: the error code ("SESSION_SUPERSEDED", "FORBIDDEN", ...).
    stopped_code: str | None
    stopped_status: int | None


class LiveFeed:
    def __init__(self, token: str, url: str | None = None) -> None:
        self.token = token
        self.url = url or ws_url()
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] | None = None
        self._connected = False
        self._failures = 0
        self._stopped_code: str | None = None
        self._stopped_status: int | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._last_read = time.monotonic()
        self._connection: ClientConnection | None = None
        self._thread = threading.Thread(target=self._run, name="live-feed", daemon=True)
        self._thread.start()

    # --- Used by the page ---

    @property
    def alive(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    def view(self) -> FeedView:
        self._last_read = time.monotonic()
        with self._lock:
            return FeedView(
                snapshot=copy.deepcopy(self._snapshot),
                connected=self._connected,
                failures=self._failures,
                stopped_code=self._stopped_code,
                stopped_status=self._stopped_status,
            )

    def retry_now(self) -> None:
        """Skips the rest of the current back-off wait."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        connection = self._connection
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    # --- Thread ---

    def _idle(self) -> bool:
        return time.monotonic() - self._last_read > IDLE_STOP_SECONDS

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set() and not self._idle():
            got_message = self._session()
            if self._stop.is_set():
                break
            with self._lock:
                self._connected = False
                self._failures += 1
            if got_message:
                attempt = 0
            delay = BACKOFF_SECONDS[attempt] if attempt < len(BACKOFF_SECONDS) else BACKOFF_CAP_SECONDS
            attempt += 1
            self._wake.clear()
            self._wake.wait(delay)
        self._stop.set()
        with self._lock:
            self._connected = False

    def _session(self) -> bool:
        """One connection, until it drops. Returns True when at least one message arrived."""
        got_message = False
        try:
            with connect(
                self.url,
                additional_headers={"Authorization": f"Bearer {self.token}"},
                open_timeout=OPEN_TIMEOUT_SECONDS,
                close_timeout=2,
                max_size=8 * 1024 * 1024,
            ) as connection:
                self._connection = connection
                while not self._stop.is_set():
                    if self._idle():
                        self._stop.set()
                        break
                    try:
                        text = connection.recv(timeout=SILENCE_SECONDS)
                    except TimeoutError:
                        return got_message  # Silent link: reconnect.
                    message = json.loads(text)
                    got_message = True
                    self._apply(message)
        except ConnectionClosed as exc:
            self._closed(exc)
        except (InvalidStatus, WebSocketException, OSError, TimeoutError, ValueError):
            pass
        finally:
            self._connection = None
        return got_message

    def _closed(self, exc: ConnectionClosed) -> None:
        frame = exc.rcvd
        if frame is None:
            return
        if frame.code in (AUTH_LOST_CODE, FORBIDDEN_CODE):
            with self._lock:
                self._stopped_code = frame.reason or ("UNAUTHORIZED" if frame.code == AUTH_LOST_CODE else "FORBIDDEN")
                self._stopped_status = 401 if frame.code == AUTH_LOST_CODE else 403
            self._stop.set()

    # --- Merging pushed messages ---

    def _apply(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        with self._lock:
            if kind == "snapshot":
                message.pop("type", None)
                self._snapshot = message
                self._connected = True
                self._failures = 0
            elif kind == "readings" and self._snapshot is not None:
                merge_readings(self._snapshot, message)
            elif kind == "heartbeat" and self._snapshot is not None:
                merge_heartbeat(self._snapshot, message)


def merge_readings(snapshot: dict[str, Any], message: dict[str, Any]) -> None:
    """Folds one `readings` message into a LiveSnapshot-shaped dict, in place."""
    snapshot["server_time"] = message["server_time"]
    snapshot["summary"] = message["summary"]
    counts = message.get("category_anomaly_counts_5m") or {}
    by_name = {category["category"]: category for category in snapshot["categories"]}
    anomalies = []
    for reading in message["readings"]:
        category = by_name.get(reading["category"])
        point = {"value": reading["value"], "timestamp": reading["timestamp"], "is_anomaly": reading["is_anomaly"]}
        if category is not None:
            category["readings"].append(point)
            category["latest"] = point
            category["range"] = reading["range"]
            category["range_status"] = "set" if reading["range"] else "unset"
            category["unit"] = reading["unit"]
        if reading["is_anomaly"]:
            anomalies.append(
                {
                    "category": reading["category"],
                    "title": reading["title"],
                    "value": reading["value"],
                    "unit": reading["unit"],
                    "range": reading["range"],
                    "timestamp": reading["timestamp"],
                }
            )
    for category in snapshot["categories"]:
        category["anomaly_count_5m"] = counts.get(category["category"], category.get("anomaly_count_5m", 0))
    if anomalies:
        anomalies.sort(key=lambda item: item["timestamp"], reverse=True)
        snapshot["recent_anomalies"] = (anomalies + snapshot["recent_anomalies"])[:RECENT_ANOMALY_LIMIT]
    _trim(snapshot)


def merge_heartbeat(snapshot: dict[str, Any], message: dict[str, Any]) -> None:
    snapshot["server_time"] = message["server_time"]
    snapshot["generator"] = message["generator"]
    summary = snapshot["summary"]
    last = message.get("last_reading_at") or summary.get("last_reading_at")
    summary["last_reading_at"] = last
    if last is not None:
        gap = (_parse(message["server_time"]) - _parse(last)).total_seconds()
        summary["stalled"] = gap > float(snapshot["stale_after_seconds"])
    _trim(snapshot)


def _trim(snapshot: dict[str, Any]) -> None:
    """Drops points that fell out of the window, as the WebSocket contract asks of clients."""
    cutoff = _parse(snapshot["server_time"]) - timedelta(seconds=int(snapshot["window_seconds"]))
    for category in snapshot["categories"]:
        category["readings"] = [point for point in category["readings"] if _parse(point["timestamp"]) >= cutoff]
