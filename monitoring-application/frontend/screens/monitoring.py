"""SCREEN-20: the live window (pushed over a WebSocket) and the custom time range (saved data).

The view toggle sits outside both fragments, so their reruns never reset it. Only the chosen
view's fragment is drawn: the live window is fed by a WebSocket to /api/v1/realtime/ws
(core.live_feed) and redraws every second from what has been pushed, without polling; the custom
range loads only when 「查詢」 is pressed and never updates by itself.
Both fragments write the status chip into the header slot, which every full run writes first.
A dropped connection keeps the last data on screen (重新連線中) while the feed reconnects; a lost
session (close code 4401) goes to SCREEN-01 through session.lose_session. Layout order is also the narrow order: summary, latest readings, small
charts, anomaly counts, list (Streamlit stacks columns in source order).
"""

import calendar
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta
from streamlit.errors import StreamlitAPIException

from core import api, charts, live_feed, records

_LIVE, _RANGE = "即時窗口", "自訂時間範圍"
_VIEW = "monitoring_view"
_START = "monitoring_range_start"
_END = "monitoring_range_end"
# Under the prefix forms.reset_interrupted_submits() clears: changing page does not keep them.
_LIVE_KEY = "__submit__monitoring_live"
_RANGE_KEY = "__submit__monitoring_range"

_REDRAW_SECONDS = 1
_FEED_KEY = "monitoring_live_feed"
_NEW_HINT_SECONDS = 5
_RANGE_TIMEOUT_SECONDS = 30
_RETRY_AFTER_FAILURES = 3
_MAX_MONTHS = 3
_RANGE_PATH = "/api/v1/realtime/range"
_EMPTY = "—"
_ANOMALY_ROW_STYLE = "background-color: #FBE3E0"
_ANOMALY_CELL_STYLE = "background-color: #FBE3E0; color: #A61B12; font-weight: 700"

_RECONNECTING = (
    "即時連線中斷，正在自動重試。恢復後會顯示最近 5 分鐘的資料；更早的資料可用「自訂時間範圍」查看。"
)
_RANGE_LOAD_ERROR = "無法載入資料，已保留你的時間範圍。"
_TOO_LONG = "時間範圍超過 3 個月上限。請縮短範圍，或分批查詢。"


# --- Formatting ---


def _local(text: str) -> datetime:
    return records.parse_timestamp(text).astimezone(records.display_zone())


def _clock(text: str) -> str:
    """HH:MM:SS, with the date in front when it is not today on the viewer's clock."""
    value = _local(text)
    today = datetime.now(records.display_zone()).date()
    return value.strftime("%H:%M:%S") if value.date() == today else value.strftime("%Y-%m-%d %H:%M:%S")


def _minute(value: datetime) -> str:
    return value.astimezone(records.display_zone()).strftime("%Y-%m-%d %H:%M")


def _range_text(range_: dict[str, float] | None, unit: str | None) -> str:
    if not range_:
        return "未設定範圍"
    text = f"{records.format_number(float(range_['min']))} – {records.format_number(float(range_['max']))}"
    return f"{text} {unit}" if unit else text


def _value(value: float, unit: str | None) -> str:
    return records.format_value(float(value), unit)


def _status(is_anomaly: bool) -> str:
    return "◆ 異常" if is_anomaly else "正常"


def _wall_ms(value: datetime) -> int:
    """The viewer's wall-clock time as if it were UTC, for a chart on a UTC scale."""
    return calendar.timegm(value.astimezone(records.display_zone()).timetuple()) * 1000


# --- State ---


@dataclass
class LiveState:
    snapshot: dict[str, Any] | None = None
    failures: int = 0
    error: str | None = None
    newest: str | None = None
    new_after: str | None = None
    new_until: float = 0.0


@dataclass(frozen=True)
class RangeRequest:
    start: datetime
    end: datetime


@dataclass
class RangeState:
    pending: RangeRequest | None = None
    failed: RangeRequest | None = None
    shown: RangeRequest | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    field_error: str | None = None
    hint: str | None = None


def _live_state() -> LiveState:
    return st.session_state.setdefault(_LIVE_KEY, LiveState())


def _range_state() -> RangeState:
    return st.session_state.setdefault(_RANGE_KEY, RangeState())


def _rerun_fragment() -> None:
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:  # Raised during a full run, where only a full rerun is allowed.
        st.rerun()


# --- Shared drawing ---


def _tile(label: str, value: str, *, anomaly: bool = False, note: str | None = None) -> None:
    with st.container(border=True, height="stretch"):
        st.metric(label, value)
        if anomaly:
            st.badge("◆ 異常", color="red")
        if note:
            st.caption(note)


def _summary(labels: tuple[str, str, str], summary: dict[str, Any], count_key: str) -> None:
    """The time tile shows the clock large and the date under it, so a narrow tile never cuts it."""
    anomalous = summary["anomalous_category_count"]
    first, second, third = st.columns(3)
    with first:
        value = f"◆ {anomalous} / {summary['category_count']}" if anomalous else f"0 / {summary['category_count']}"
        _tile(labels[0], value, anomaly=anomalous > 0)
    with second:
        _tile(labels[1], str(summary[count_key]))
    last = summary["last_reading_at"]
    with third:
        if last:
            value = _local(last)
            _tile(labels[2], value.strftime("%H:%M:%S"), note=value.strftime("%Y-%m-%d"))
        else:
            _tile(labels[2], _EMPTY)


def _position_rows(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for category in categories:
        latest = category["latest"]
        bounds = charts.bounds_of(category["range"])
        if latest is None:
            note = "" if bounds else "未設定範圍"
            rows.append({"name": category["category"], "position": None, "anomaly": False, "value_text": "尚無讀數", "note": note})
            continue
        position = None
        if bounds is not None:
            position = (float(latest["value"]) - bounds.low) / (bounds.high - bounds.low)
        rows.append(
            {
                "name": category["category"],
                "position": position,
                "anomaly": bool(latest["is_anomaly"]),
                "value_text": _value(latest["value"], category["unit"]),
                "note": "" if bounds else "未設定範圍",
            }
        )
    return rows


def _latest_card(title: str, categories: list[dict[str, Any]]) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**　位置 = 在最小值到最大值之間的哪裡")
        st.altair_chart(charts.position_bars(_position_rows(categories)), width="stretch", alt=f"{title}位置條")
        if any(category["range"] is None for category in categories):
            st.caption("未設定範圍的分類只顯示讀數，不畫位置條，也不判斷異常。")


def _counts_card(title: str, categories: list[dict[str, Any]], count_key: str, none_text: str) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        rows = [{"name": category["category"], "count": category[count_key]} for category in categories]
        st.altair_chart(charts.anomaly_count_bars(rows), width="stretch", alt=title)
        if not any(row["count"] for row in rows):
            st.caption(none_text)


def _chart_header(category: dict[str, Any], latest: dict[str, Any] | None) -> None:
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        name = records.escape_markdown(category["category"])
        unit = f" {records.escape_markdown(category['unit'])}" if category["unit"] else ""
        st.markdown(f"**{name}**{unit}", width="content")
        with st.container(horizontal=True, width="content", vertical_alignment="center", gap="small"):
            if category["range"] is None:
                st.badge("未設定範圍", color="orange")
            if latest is not None:
                text = records.escape_markdown(_value(latest["value"], category["unit"]))
                st.markdown(f":red[**◆ 異常**] {text}" if latest["is_anomaly"] else text, width="content")


def _anomaly_list(
    items: list[dict[str, Any]], *, heading: str, new_after: str | None, empty_title: str, empty_text: str
) -> None:
    st.subheader(heading, anchor=False)
    if not items:
        with st.container(border=True, horizontal_alignment="center"):
            st.markdown(f"**{empty_title}**", text_alignment="center")
            st.text(empty_text, text_alignment="center")
        return
    threshold = records.parse_timestamp(new_after) if new_after else None
    rows = []
    for item in items:
        is_new = threshold is not None and records.parse_timestamp(item["timestamp"]) > threshold
        rows.append(
            {
                "時間": _clock(item["timestamp"]) + ("  新" if is_new else ""),
                "分類": item["category"],
                "數值": f"◆ 異常 {_value(item['value'], item['unit'])}",
                "範圍": _range_text(item["range"], item["unit"]),
            }
        )
    frame = pd.DataFrame(rows, columns=["時間", "分類", "數值", "範圍"])
    styled = frame.style.apply(
        lambda row: [_ANOMALY_CELL_STYLE if column == "數值" else _ANOMALY_ROW_STYLE for column in row.index], axis=1
    )
    st.dataframe(
        styled,
        hide_index=True,
        width="stretch",
        column_config={
            "時間": st.column_config.TextColumn("時間", width="medium"),
            "分類": st.column_config.TextColumn("分類", width="small"),
            "數值": st.column_config.TextColumn("數值", width="medium"),
            "範圍": st.column_config.TextColumn("範圍", width="medium"),
        },
        alt="最近異常清單：時間、分類、數值與範圍，最新在前",
    )
    st.caption(f"時間以 {records.display_zone().key} 顯示；最多 20 筆，最新在前。")


def _empty_state(title: str, text: str) -> None:
    with st.container(border=True, horizontal_alignment="center"):
        st.markdown(f"**{title}**", text_alignment="center")
        st.text(text, text_alignment="center")


def _skeleton(chart_count: int = 4, *, with_side: bool = True) -> None:
    if with_side:
        for column in st.columns(3):
            with column:
                st.skeleton(height=104)
        st.skeleton(height=170)
    main, side = st.columns([2, 1]) if with_side else (st.container(), None)
    with main:
        for _ in range(chart_count):
            st.skeleton(height=220)
    if side is not None:
        with side:
            st.skeleton(height=190)
        st.skeleton(height=200)


# --- Live window ---


def _feed() -> live_feed.LiveFeed | None:
    """This session's WebSocket feed, started on first use and replaced when the login changes."""
    token = st.session_state.get(api.TOKEN_STATE_KEY)
    feed: live_feed.LiveFeed | None = st.session_state.get(_FEED_KEY)
    if feed is not None and (feed.token != token or not feed.alive) and feed.view().stopped_code is None:
        feed.stop()
        feed = None
    if feed is None and token:
        feed = live_feed.LiveFeed(token)
        st.session_state[_FEED_KEY] = feed
    return feed


def _pull(state: LiveState) -> None:
    """Copies what the WebSocket has pushed so far into the page state."""
    feed = _feed()
    if feed is None:
        return
    if st.session_state.get("monitoring_live_retry"):
        feed.retry_now()
    view = feed.view()
    if view.stopped_code is not None:
        st.session_state.pop(_FEED_KEY, None)
        if view.stopped_status == 401:
            from core import session  # session imports api, which this page also uses.

            session.lose_session(view.stopped_code)
            return
        state.error = "你的角色不能檢視即時監控。"
        return
    state.failures = 0 if view.connected else view.failures
    state.error = None
    if view.snapshot is None:
        return
    items = view.snapshot["recent_anomalies"]
    newest = items[0]["timestamp"] if items else state.newest
    # A row is 「新」 for the one update that brought it (the static form of SHARED-07's hint).
    # Redraws come every second, so the hint stays for _NEW_HINT_SECONDS rather than one redraw.
    if newest != state.newest:
        state.new_after = state.newest if state.snapshot is not None else None
        state.new_until = time.monotonic() + _NEW_HINT_SECONDS
    elif time.monotonic() >= state.new_until:
        state.new_after = None
    state.newest = newest
    state.snapshot = view.snapshot


def _live_chip(chip: Any, state: LiveState) -> None:
    snapshot = state.snapshot
    last = snapshot["summary"]["last_reading_at"] if snapshot else None
    last_text = f" · 最後資料 {_clock(last)}" if last else ""
    if state.failures:
        chip.badge(f"重新連線中…{last_text}", icon=":material/sync:", color="orange")
    elif snapshot is None:
        chip.badge("連線中…", color="gray")
    elif snapshot["summary"]["stalled"]:
        chip.badge(f"已暫停更新{last_text}", icon=":material/pause_circle:", color="gray")
    elif last is None:
        chip.badge("已連線 · 等待第一筆資料", icon=":material/radio_button_checked:", color="blue")
    else:
        chip.badge(f"已連線{last_text}", icon=":material/radio_button_checked:", color="blue")


def _live_message(state: LiveState) -> None:
    """One fixed slot for every live message, so nothing below it moves when one appears."""
    slot = st.empty()
    snapshot = state.snapshot
    if state.failures:
        with slot.container(horizontal=True, vertical_alignment="center"):
            st.warning(_RECONNECTING, icon=":material/sync:")
            if state.failures >= _RETRY_AFTER_FAILURES:
                st.button("重試", key="monitoring_live_retry", width="content")
    elif state.error:
        slot.error(state.error)
    elif snapshot and snapshot["summary"]["stalled"]:
        last = _clock(snapshot["summary"]["last_reading_at"])
        slot.warning(f"資料已停止到達，目前顯示的是最後收到的資料（{last}）。**這不代表沒有異常。**", icon=":material/pause_circle:")
    elif snapshot and snapshot["summary"]["last_reading_at"] is None:
        slot.info("連線成功，等待第一筆資料…")


def _live_rows(category: dict[str, Any], server_time: datetime, window: int) -> list[dict[str, Any]]:
    rows = []
    for reading in category["readings"]:
        offset = (records.parse_timestamp(reading["timestamp"]) - server_time).total_seconds()
        if -window <= offset <= 0:
            rows.append(
                {
                    "offset": offset,
                    "value": float(reading["value"]),
                    "anomaly": bool(reading["is_anomaly"]),
                    "time": records.format_timestamp(reading["timestamp"]),
                    "value_text": _value(reading["value"], category["unit"]),
                    "status": f"{_status(reading['is_anomaly'])}（範圍 {_range_text(category['range'], category['unit'])}）",
                }
            )
    return rows


def _draw_live(state: LiveState) -> None:
    snapshot = state.snapshot
    assert snapshot is not None
    summary = snapshot["summary"]
    if summary["last_reading_at"] is None:
        interval = records.format_number(float(snapshot["interval_seconds"]))
        _empty_state("等待第一筆資料", f"系統啟動後會自動每 {interval} 秒為每個設備產生一筆模擬資料。")
        return
    dimmed = bool(state.failures) or summary["stalled"]
    categories = snapshot["categories"]
    window = int(snapshot["window_seconds"])
    _summary(
        ("目前異常的分類數", "最近 5 分鐘異常筆數", "最後收到資料時間"),
        summary,
        "anomaly_count_5m",
    )
    _latest_card("最新讀數", categories)
    server_time = records.parse_timestamp(snapshot["server_time"])
    main, side = st.columns([2, 1])
    with main:
        for category in categories:
            with st.container(border=True):
                _chart_header(category, category["latest"])
                chart = charts.live_chart(
                    _live_rows(category, server_time, window),
                    charts.bounds_of(category["range"]),
                    window_seconds=window,
                    dimmed=dimmed,
                    title=category["category"],
                )
                st.altair_chart(chart, width="stretch", alt=f"{category['category']}最近 5 分鐘折線圖")
    with side:
        _counts_card("異常次數（近 5 分鐘）柱狀圖", categories, "anomaly_count_5m", "最近 5 分鐘沒有異常")
    _anomaly_list(
        snapshot["recent_anomalies"],
        heading="最近異常",
        new_after=state.new_after,
        empty_title="最近沒有異常",
        empty_text="新的異常會出現在這裡。",
    )


@st.fragment(run_every=_REDRAW_SECONDS)
def _live_view(chip: Any) -> None:
    state = _live_state()
    _pull(state)
    _live_chip(chip, state)
    _live_message(state)
    if state.snapshot is None:
        if not state.error:
            _skeleton()
        return
    _draw_live(state)


# --- Custom time range ---


def _default_range() -> None:
    now = datetime.now(records.display_zone()).replace(second=0, microsecond=0)
    st.session_state.setdefault(_START, records.to_widget_time(now - timedelta(hours=1)))
    st.session_state.setdefault(_END, records.to_widget_time(now))


def _latest_allowed_end(start: datetime) -> datetime:
    """The backend's rule: 3 calendar months after start, a missing day clamped to the month's end."""
    return start + relativedelta(months=_MAX_MONTHS)


def _submit_range() -> None:
    state = _range_state()
    start = records.from_widget_time(st.session_state.get(_START))
    end = records.from_widget_time(st.session_state.get(_END))
    state.error = state.field_error = state.hint = None
    if start is None or end is None:
        state.field_error = "請選擇開始與結束時間。"
        return
    if start >= end:
        state.field_error = "開始時間必須早於結束時間。"
        return
    latest = _latest_allowed_end(start)
    if end > latest:
        # Refused before sending (PRD-08-R13); the backend refuses it too.
        state.field_error = _TOO_LONG
        state.hint = f"從 {_minute(start)} 起，結束時間最晚為 {_minute(latest)}。"
        return
    state.pending = RangeRequest(start, end)
    state.failed = None


def _retry_range() -> None:
    state = _range_state()
    if state.failed:
        state.pending, state.failed, state.error = state.failed, None, None


def _run_range(state: RangeState) -> None:
    request = state.pending
    assert request is not None
    state.pending = None
    try:
        body = api.get(
            _RANGE_PATH,
            params={"start": request.start.isoformat(), "end": request.end.isoformat()},
            timeout=_RANGE_TIMEOUT_SECONDS,
        )
    except api.BackendUnavailable:
        state.failed, state.error = request, _RANGE_LOAD_ERROR
        return
    except api.ApiError as exc:
        if exc.code == "RANGE_TOO_LONG":
            state.field_error = _TOO_LONG
            latest = (exc.details or {}).get("latest_allowed_end") if isinstance(exc.details, dict) else None
            state.hint = f"結束時間最晚為 {_minute(records.parse_timestamp(latest))}。" if latest else exc.message
        elif exc.status == 422:
            state.field_error = next(iter(exc.field_errors().values()), exc.message)
        elif exc.status >= 500:
            state.failed, state.error = request, _RANGE_LOAD_ERROR
        else:
            state.error = exc.message
        return
    state.result = body
    state.shown = request


def _range_form(state: RangeState) -> None:
    busy = state.pending is not None
    with st.container(border=True):
        with st.form("monitoring_range_form", border=False, enter_to_submit=False):
            with st.container(horizontal=True, vertical_alignment="bottom"):
                # Inputs stay editable while a query runs (CH: 範圍欄位可改).
                st.datetime_input("開始時間", key=_START, step=60, format="YYYY-MM-DD", width=230)
                st.datetime_input("結束時間", key=_END, step=60, format="YYYY-MM-DD", width=230)
                st.form_submit_button(
                    "查詢中…" if busy else "查詢", type="primary", disabled=busy, on_click=_submit_range
                )
        # Fixed slots: validation, then load errors with their retry.
        field_slot = st.empty()
        if state.field_error:
            with field_slot.container():
                st.error(state.field_error)
                if state.hint:
                    st.caption(state.hint)
        error_slot = st.empty()
        if state.error:
            with error_slot.container(horizontal=True, vertical_alignment="center"):
                st.error(state.error)
                if state.failed:
                    st.button("重試", key="monitoring_range_retry", on_click=_retry_range, width="content")
        st.caption(f"時間以 {records.display_zone().key} 顯示與輸入；單次最長 3 個月（剛好 3 個月可以查詢）。")


def _range_rows(category: dict[str, Any], aggregated: bool) -> list[dict[str, Any]]:
    bounds = charts.bounds_of(category["range"])
    unit = category["unit"]
    rows = []
    for point in category["points"]:
        low, high = float(point["min"]), float(point["max"])
        anomaly = point["anomaly_count"] > 0
        # The end that crossed the range carries the mark, so averaging cannot hide it (PRD-11-R9).
        mark = low if bounds is not None and high < bounds.high and low <= bounds.low else high
        start = records.parse_timestamp(point["bucket_start"])
        rows.append(
            {
                "t": _wall_ms(start),
                "avg": float(point["avg"]),
                "min": low,
                "max": high,
                "mark": mark,
                "anomaly": anomaly,
                "time": records.format_timestamp(start),
                "avg_text": _value(round(float(point["avg"]), 3), unit) if aggregated else _value(point["avg"], unit),
                "spread_text": f"{_value(low, unit)} – {_value(high, unit)}",
                "count": point["count"],
                "anomaly_count": point["anomaly_count"],
            }
        )
    return rows


def _draw_range_result(state: RangeState) -> None:
    result, shown = state.result, state.shown
    assert result is not None and shown is not None
    categories = result["categories"]
    span = f"{_minute(shown.start)} — {_minute(shown.end)}"
    if not any(category["points"] for category in categories):
        _empty_state("這段時間沒有資料", f"{_minute(shown.start)} – {_minute(shown.end)} 內沒有已保存的記錄。")
        return
    aggregation = result["aggregation"]
    aggregated = aggregation["bucket_seconds"] is not None
    note = f"資料量較大，已彙總為{aggregation['label']}（異常點保留標示）。" if aggregated else "逐筆顯示。"
    st.info(f"檢視已保存資料：**{span}**。{note}此檢視不隨新資料自動更新。")
    summary = result["summary"]
    _summary(
        ("範圍內曾異常的分類數", "範圍內異常筆數", "範圍內最後一筆資料時間"),
        summary,
        "anomaly_count",
    )
    _latest_card("範圍內最後一筆讀數", categories)
    main, side = st.columns([2, 1])
    with main:
        for category in categories:
            with st.container(border=True):
                _chart_header(category, category["latest"])
                if not category["points"]:
                    st.caption("此分類在這段時間沒有資料。")
                    continue
                chart = charts.range_chart(
                    _range_rows(category, aggregated),
                    charts.bounds_of(category["range"]),
                    start_ms=_wall_ms(shown.start),
                    end_ms=_wall_ms(shown.end),
                    aggregated=aggregated,
                    title=category["category"],
                )
                st.altair_chart(chart, width="stretch", alt=f"{category['category']}在 {span} 的折線圖")
    with side:
        _counts_card("異常次數（所選範圍）柱狀圖", categories, "anomaly_count", "這段時間沒有異常")
    _anomaly_list(result["recent_anomalies"], heading="範圍內最近的異常", new_after=None, empty_title="這段時間沒有異常", empty_text="範圍內沒有被標為異常的記錄。")


@st.fragment
def _range_view(chip: Any) -> None:
    chip.badge("檢視已保存資料（不自動更新）", icon=":material/history:", color="gray")
    state = _range_state()
    _default_range()
    _range_form(state)
    if state.pending:
        st.info("查詢中…顯示的是舊結果（若有）。")
        _skeleton(with_side=False)
        _run_range(state)
        _rerun_fragment()
    if state.result is None:
        text = "修正時間範圍後按「查詢」。" if state.field_error else "選擇開始與結束時間後按「查詢」。"
        _empty_state("尚未查詢", text)
        return
    _draw_range_result(state)


# --- Page ---


def _page() -> None:
    st.session_state.setdefault(_VIEW, _LIVE)
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.title("即時監控", anchor=False, width="content")
        with st.container(horizontal=True, width="content", vertical_alignment="center"):
            view = st.segmented_control("檢視", [_LIVE, _RANGE], key=_VIEW, required=True, label_visibility="collapsed")
            chip = st.empty()
    # Every full run writes the slot first, which lets the fragments redraw it on their reruns.
    chip.badge("載入中…", color="gray")
    if view == _RANGE:
        _range_view(chip)
    else:
        _live_view(chip)


_page()
