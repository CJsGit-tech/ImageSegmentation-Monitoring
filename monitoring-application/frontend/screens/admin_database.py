"""SCREEN-42: database status banner and metrics, refreshed every 10 seconds (PRD-15).

The whole screen is one fragment with run_every, so a refresh never runs the shell: a full run
calls /auth/me, which fails with 503 during a database outage and would replace the page with
SCREEN-04 instead of the red banner. Any failed update counts as unavailable (spec: 更新失敗視同
不可用); every metric then shows 「—」 and the tiles keep their size. An automatic update draws
nothing until its answer arrives, so old values stay on screen without a skeleton.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import streamlit as st

from core import api, records

_PATH = "/api/v1/db-status"
_REFRESH_SECONDS = 10
# The backend gives up on the database after about 2 seconds; this must stay under the interval.
_TIMEOUT_SECONDS = 5
_STATE_KEY = "__submit__db_status"
_EMPTY = "—"
_BLANK = " "

_DOWN = "**資料庫目前不可用**：無法連線到資料庫。系統會每 10 秒重試，恢復後此橫幅自動消失。"
_SYSTEM_UNREACHABLE = "無法連線到系統"


@dataclass
class State:
    body: dict[str, Any] | None = None  # The last 200 answer; None after a failed request.
    available: bool = False
    detail: str | None = None
    updated_at: datetime | None = None
    last_available_at: datetime | None = None
    refresh_requested: bool = False


def _state() -> State:
    return st.session_state.setdefault(_STATE_KEY, State())


def _request_refresh() -> None:
    _state().refresh_requested = True


# --- Formatting ---


def _utc(text: str) -> datetime:
    """The backend writes naive UTC; a value without an offset is UTC, not the server's zone."""
    value = records.parse_timestamp(text)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _local(value: datetime) -> datetime:
    return value.astimezone(records.display_zone())


def _clock(value: datetime) -> str:
    """HH:MM:SS, with the date in front when it is not today on the viewer's clock."""
    local = _local(value)
    today = datetime.now(records.display_zone()).date()
    return local.strftime("%H:%M:%S") if local.date() == today else local.strftime("%Y-%m-%d %H:%M:%S")


def _milliseconds(value: float) -> str:
    return f"{value:.1f} ms" if value < 10 else f"{value:.0f} ms"


def _warning(message: str) -> str:
    return f":material/warning: {records.escape_markdown(message)}"


# --- Data ---


def _failed(state: State, detail: str) -> None:
    state.body = None
    state.available = False
    state.detail = detail
    state.updated_at = datetime.now(timezone.utc)


def _check(state: State) -> None:
    try:
        body = api.get(_PATH, timeout=_TIMEOUT_SECONDS)
    except api.BackendUnavailable:  # Includes BackendTimeout.
        _failed(state, _SYSTEM_UNREACHABLE)
        return
    except api.ApiError as exc:  # A lost session has already gone to SCREEN-01 in api.request.
        if exc.status == 403:
            # The role changed: a full run lets the shell show SCREEN-04 with the new role.
            st.rerun(scope="app")
        _failed(state, exc.message)  # 503 DATABASE_UNAVAILABLE and any other failure.
        return
    state.body = body
    state.available = body["status"] == "available"
    state.updated_at = _utc(body["checked_at"])
    error = body.get("error")
    state.detail = error["message"] if error else None
    if state.available:
        state.last_available_at = state.updated_at
    elif body.get("last_available_at"):
        state.last_available_at = _utc(body["last_available_at"])


# --- Drawing ---


def _header() -> tuple[Any, Any]:
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.title("資料庫狀態", anchor=False, width="content")
        with st.container(horizontal=True, width="content", vertical_alignment="center"):
            caption = st.empty()
            button = st.empty()
    return caption, button


def _updated_caption(slot: Any, state: State) -> None:
    when = _clock(state.updated_at) if state.updated_at else _EMPTY
    slot.caption(f"最後更新 {when}（每 {_REFRESH_SECONDS} 秒自動更新）", width="content")


def _banner(state: State) -> None:
    slot = st.empty()
    if state.available:
        with slot.container(border=True):
            st.markdown("**資料庫可用**")
        return
    parts = [records.escape_markdown(state.detail)] if state.detail else []
    if state.last_available_at:
        parts.append(f"最後可用 {_clock(state.last_available_at)}")
    text = f"{_DOWN}  \n{' · '.join(parts)}" if parts else _DOWN
    slot.error(text, icon=":material/error:")


def _tile(label: str, value: str, caption: str | None = None, *, progress: float | None = None) -> None:
    """The caption is always drawn and tiles in a row share one height, so 「—」 keeps the layout."""
    with st.container(border=True, height="stretch"):
        st.metric(label, value)
        if progress is not None:
            st.progress(min(max(progress, 0.0), 1.0))
        st.caption(caption or _BLANK)


def _join(*lines: str | None) -> str | None:
    kept = [line for line in lines if line]
    return "  \n".join(kept) if kept else None


def _tiles(state: State) -> None:
    body = state.body if state.available else None
    warnings = {item["code"]: _warning(item["message"]) for item in (body or {}).get("warnings", [])}
    first, second, third = st.columns(3)
    fourth, fifth, _ = st.columns(3)

    with first:
        _tile("連線狀態", "已連線" if body else _EMPTY)

    with second:
        response = body["response_time_ms"] if body else None
        _tile("回應時間", _milliseconds(float(response)) if response is not None else _EMPTY, warnings.get("SLOW_RESPONSE"))

    with third:
        if body:
            pool = body["pool"]
            _tile(
                "連線池使用狀況",
                # 「使用中」 sits in the caption: st.metric cuts a long value off in a narrow tile.
                f"{pool['in_use']} / {pool['capacity']}",
                _join(f"使用中 / 上限 · 閒置 {pool['idle']} · 已開啟 {pool['open']}", warnings.get("POOL_HIGH")),
                progress=float(pool["usage_ratio"]),
            )
        else:
            # An empty bar keeps the tile as tall as when it is available, on a narrow screen too.
            _tile("連線池使用狀況", _EMPTY, progress=0.0)

    with fourth:
        count = body["record_count"] if body else None
        as_of = body["record_count_as_of"] if body else None
        note = f"統計於 {_clock(_utc(as_of))}（最多每 60 秒更新）" if as_of else None
        _tile("資料筆數", f"{count:,}" if count is not None else _EMPTY, note)

    with fifth:
        last = body["last_batch_write_at"] if body else None
        if last:
            value = _local(_utc(last))
            today = datetime.now(records.display_zone()).date()
            date = value.strftime("%Y-%m-%d") if value.date() != today else None
            _tile("最後一次批次寫入時間", value.strftime("%H:%M:%S"), _join(date, warnings.get("BATCH_STALE")))
        else:
            _tile("最後一次批次寫入時間", _EMPTY, "尚無系統產生的資料" if body else None)


@st.fragment(run_every=_REFRESH_SECONDS)
def _screen() -> None:
    state = _state()
    manual = state.refresh_requested
    state.refresh_requested = False
    if not manual:
        # Ask first and draw after: the previous values stay visible while the request runs.
        _check(state)
    caption, button = _header()
    if manual:
        _updated_caption(caption, state)
        button.button("重新整理中…", icon="spinner", disabled=True, key="db_status_refresh_busy")
        _check(state)
    _updated_caption(caption, state)
    button.button("重新整理", key="db_status_refresh", on_click=_request_refresh)
    _banner(state)
    _tiles(state)


_screen()
