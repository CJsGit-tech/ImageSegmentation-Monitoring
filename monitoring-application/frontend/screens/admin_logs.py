"""SCREEN-41: Admin reads the system log by time, event type, severity and account (PRD-14).

The list is one fragment, like SCREEN-10 and SCREEN-40: querying and paging do not re-run the
shell's login check. The first visit queries the past 24 hours. An empty 結束時間 means 「現在」,
resolved again at each 查詢, so a new event shows up after querying again; paging keeps the
resolved end, because the backend's cursor belongs to the exact conditions.

A range over 3 months is refused before sending, with the backend's rule (core/time_range.py):
the same wall clock 3 calendar months later in start's offset, so exactly 3 months is allowed.

Read only (PRD-14-R3): no control changes or deletes a log row. Summaries, account names and
details are data, shown through st.dataframe, st.text and st.json, never as Markdown.
"""

import calendar
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

from core import api
from core.records import display_zone, format_timestamp, from_widget_time, parse_timestamp, to_widget_time

PAGE_SIZE = 25
_LOGS_PATH = "/api/v1/logs"
_OPTIONS_PATH = "/api/v1/logs/options"
_DEFAULT_HOURS = 24
_DEFAULT_MAX_MONTHS = 3
_USERNAME_MAX_LENGTH = 64  # The backend's USERNAME_MAX_LENGTH.
_SKELETON_ROWS = 6
_SKELETON_ROW_HEIGHT = 32
_FIELD_TIME = 215
_FIELD_CHOICE = 170
_FIELD_ACCOUNT = 140
_NONE = "—"

_LOAD_ERROR = "無法載入日誌，已保留你的條件。"
_OPTIONS_ERROR = "無法載入事件類型與嚴重程度的選項；仍可依時間與使用者查詢。"
_START_MISSING = "請選擇開始時間。"
_REVERSED = "開始時間必須早於結束時間。"
_TOO_LONG = "時間範圍超過 3 個月上限，請縮短範圍。"
_EMPTY = "這個條件內沒有日誌"

_SEVERITY_STYLES = {
    "warning": "color: #9A5B00; font-weight: 600",
    "error": "color: #A61B12; font-weight: 700",
}

# Under the prefix forms.reset_interrupted_submits() clears: changing page resets the screen.
_STATE_KEY = "__submit__admin_logs"
_START = "admin_logs_start"
_END = "admin_logs_end"
_TYPES = "admin_logs_event_types"
_SEVERITIES = "admin_logs_severities"
_ACCOUNT = "admin_logs_username"

_COLUMNS = {
    "時間": st.column_config.TextColumn("時間", width="medium"),
    "事件類型": st.column_config.TextColumn("事件類型", width="small"),
    "嚴重程度": st.column_config.TextColumn("嚴重程度", width="small"),
    "使用者": st.column_config.TextColumn("使用者", width="small"),
    "說明": st.column_config.TextColumn("說明", width="large"),
}


# --- Conditions ---


def _query(start: datetime, end: datetime, end_is_now: bool, types: Any, severities: Any, username: str | None) -> dict[str, Any]:
    """A plain dict, not a dataclass: the page script redefines its classes on every rerun."""
    return {
        "start": start,
        "end": end,
        "end_is_now": end_is_now,
        "event_types": tuple(types or ()),
        "severities": tuple(severities or ()),
        "username": username,
    }


def _params(query: dict[str, Any], cursor: str | None) -> dict[str, Any]:
    """requests repeats list values: ?event_type=a&event_type=b."""
    params: dict[str, Any] = {"start": query["start"].isoformat(), "end": query["end"].isoformat(), "limit": PAGE_SIZE}
    if query["event_types"]:
        params["event_type"] = list(query["event_types"])
    if query["severities"]:
        params["severity"] = list(query["severities"])
    if query["username"]:
        params["username"] = query["username"]
    if cursor:
        params["cursor"] = cursor
    return params


def _now_end() -> datetime:
    """「現在」: the next whole minute, so events saved earlier in the current minute are included."""
    return datetime.now(display_zone()).replace(second=0, microsecond=0) + timedelta(minutes=1)


def _default_query() -> dict[str, Any]:
    now = datetime.now(display_zone()).replace(second=0, microsecond=0)
    return _query(now - timedelta(hours=_DEFAULT_HOURS), _now_end(), True, (), (), None)


def _minute(value: datetime) -> str:
    return value.astimezone(display_zone()).strftime("%Y-%m-%d %H:%M")


def _add_months(value: datetime, months: int) -> datetime:
    """The backend's rule (core/time_range.py): same wall clock in the value's own offset, the day
    clamped to the month's end."""
    index = value.month - 1 + months
    year, month = value.year + index // 12, index % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def _latest_allowed_end(start: datetime, months: int) -> datetime:
    # A fixed offset, as the backend uses: a zone with DST would move the wall clock by an hour.
    offset = start.utcoffset() or timedelta(0)
    return _add_months(start.astimezone(timezone(offset)), months)


# --- State ---


@dataclass
class _Request:
    """One page to load. It only becomes the current page when the backend answers."""

    query: dict[str, Any]
    stack: tuple[str | None, ...] = (None,)


@dataclass
class _State:
    query: dict[str, Any] | None = None
    stack: tuple[str | None, ...] = (None,)
    page: dict[str, Any] | None = None
    pending: _Request | None = field(default_factory=lambda: _Request(_default_query()))
    failed: _Request | None = None
    error: str | None = None
    field_error: str | None = None
    hint: str | None = None
    notice: str | None = None
    generation: int = 0
    options: dict[str, Any] | None = None


def _state() -> _State:
    return st.session_state.setdefault(_STATE_KEY, _State())


def _start(state: _State, request: _Request, notice: str | None = None) -> None:
    state.pending, state.failed, state.error, state.field_error, state.hint, state.notice = request, None, None, None, None, notice


def _load_options(state: _State) -> None:
    """Once per visit; a failure is retried on the next full run and does not stop querying."""
    if state.options is not None:
        return
    try:
        state.options = api.get(_OPTIONS_PATH)
    except (api.BackendUnavailable, api.ApiError):
        state.options = None


def _labels(state: _State, name: str) -> dict[str, str]:
    options = state.options or {}
    return {item["value"]: item["label"] for item in options.get(name, [])}


def _max_months(state: _State) -> int:
    return int((state.options or {}).get("max_range_months") or _DEFAULT_MAX_MONTHS)


# --- Actions (button callbacks) ---


def _submit() -> None:
    state = _state()
    state.field_error = state.hint = None
    start = from_widget_time(st.session_state.get(_START))
    chosen_end = from_widget_time(st.session_state.get(_END))
    if start is None:
        state.field_error = _START_MISSING
        return
    end = chosen_end or _now_end()
    if start >= end:
        state.field_error = _REVERSED
        return
    months = _max_months(state)
    latest = _latest_allowed_end(start, months)
    if end > latest:
        # Refused before sending (PRD-14-R9); the backend refuses it too. Earlier results stay.
        state.field_error = _TOO_LONG
        state.hint = f"從 {_minute(start)} 起，結束時間最晚為 {_minute(latest)}（剛好 {months} 個月可以查詢）。"
        return
    username = (st.session_state.get(_ACCOUNT) or "").strip() or None
    query = _query(start, end, chosen_end is None, st.session_state.get(_TYPES), st.session_state.get(_SEVERITIES), username)
    _start(state, _Request(query))


def _previous_page() -> None:
    state = _state()
    if state.query is not None and len(state.stack) > 1:
        _start(state, _Request(state.query, state.stack[:-1]))


def _next_page() -> None:
    state = _state()
    cursor = state.page["next_cursor"] if state.page else None
    if state.query is not None and cursor:
        _start(state, _Request(state.query, (*state.stack, cursor)))


def _retry() -> None:
    state = _state()
    if state.failed:
        _start(state, state.failed)


# --- Backend call (run after the waiting view is drawn) ---


def _run_pending(state: _State) -> None:
    while (request := state.pending) is not None:
        try:
            page = api.get(_LOGS_PATH, params=_params(request.query, request.stack[-1]))
        except api.BackendUnavailable:
            state.pending, state.failed, state.error = None, request, _LOAD_ERROR
            return
        except api.ApiError as exc:
            fields = exc.field_errors()
            if exc.code == "RANGE_TOO_LONG":
                latest = exc.details.get("latest_allowed_end") if isinstance(exc.details, dict) else None
                state.pending, state.field_error = None, _TOO_LONG
                state.hint = f"結束時間最晚為 {_minute(parse_timestamp(latest))}。" if latest else None
                return
            if exc.status == 422 and "cursor" in fields:
                state.pending, state.notice = replace(request, stack=(None,)), fields["cursor"]
                continue
            if exc.status == 422 and fields:
                state.pending, state.field_error = None, next(iter(fields.values()))
                return
            state.pending = None
            state.failed = request if exc.status >= 500 else None
            state.error = _LOAD_ERROR if exc.status >= 500 else exc.message
            return
        if not page["items"] and page["total"] > 0 and len(request.stack) > 1:
            # Rows on this page were cleaned up by retention: step back a page.
            state.pending = replace(request, stack=request.stack[:-1])
            continue
        state.query, state.stack, state.page, state.pending = request.query, request.stack, page, None
        state.generation += 1


# --- Drawing ---


def _sync_widgets(state: _State) -> None:
    """Widgets lose their values while the page is not drawn (SCREEN-04): refill from the applied query."""
    query = state.pending.query if state.pending else state.query
    if query is None:
        return
    defaults = {
        _START: to_widget_time(query["start"]),
        _END: None if query["end_is_now"] else to_widget_time(query["end"]),
        _TYPES: list(query["event_types"]),
        _SEVERITIES: list(query["severities"]),
        _ACCOUNT: query["username"] or "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _choices(state: _State, name: str, widget_key: str, applied_key: str) -> list[str]:
    """The backend's options, plus anything still chosen."""
    choices = list(_labels(state, name))
    applied = (state.query or {}).get(applied_key, ())
    for chosen in [*(st.session_state.get(widget_key) or []), *applied]:
        if chosen not in choices:
            choices.append(chosen)
    return choices


def _draw_form(state: _State) -> None:
    type_labels = _labels(state, "event_types")
    severity_labels = _labels(state, "severities")
    no_options = state.options is None
    with st.form("admin_logs_filter", border=True):
        # Fixed widths in a wrapping row: fields keep a usable size on narrow screens.
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.datetime_input("開始時間", value=None, key=_START, step=60, format="YYYY-MM-DD", width=_FIELD_TIME)
            st.datetime_input(
                "結束時間",
                value=None,
                key=_END,
                step=60,
                format="YYYY-MM-DD",
                width=_FIELD_TIME,
                help="留空表示「現在」，每次查詢時重新取得目前時間。",
            )
            st.multiselect(
                "事件類型",
                _choices(state, "event_types", _TYPES, "event_types"),
                key=_TYPES,
                format_func=lambda value: type_labels.get(value, value),
                placeholder="（無法載入選項）" if no_options else "（全部）",
                disabled=no_options,
                width=_FIELD_CHOICE,
            )
            st.multiselect(
                "嚴重程度",
                _choices(state, "severities", _SEVERITIES, "severities"),
                key=_SEVERITIES,
                format_func=lambda value: severity_labels.get(value, value),
                placeholder="（無法載入選項）" if no_options else "（全部）",
                disabled=no_options,
                width=_FIELD_CHOICE,
            )
            st.text_input(
                "使用者",
                key=_ACCOUNT,
                placeholder="（全部）",
                max_chars=_USERNAME_MAX_LENGTH,
                help="執行該操作的帳號，需完全相符（不是關鍵字搜尋）。",
                width=_FIELD_ACCOUNT,
            )
            # Stays clickable while waiting: a newer query replaces the older one (M5 section 3).
            st.form_submit_button(
                "查詢中…" if state.pending and state.page else "查詢",
                key="admin_logs_query",
                type="primary",
                on_click=_submit,
            )
        st.caption(f"時間以 {display_zone().key} 顯示與輸入，開始與結束都包含；結束時間留空表示「現在」。")


def _draw_messages(state: _State) -> None:
    """Fixed slots, always in this order, so no message leaves a stale control behind."""
    options_slot = st.empty()
    if state.options is None:
        options_slot.warning(_OPTIONS_ERROR)
    field_slot = st.empty()
    if state.field_error:
        with field_slot.container():
            st.error(state.field_error)
            if state.hint:
                st.caption(state.hint)
            if state.page is not None:
                st.caption("下方仍是上一次查詢的結果。")
    status_slot = st.empty()
    if state.pending is not None and state.page is not None:
        status_slot.info("更新中…顯示的是舊結果。")
    elif state.error:
        with status_slot.container(horizontal=True, vertical_alignment="center"):
            st.error(state.error)
            if state.failed:
                st.button("重試", key="admin_logs_retry", on_click=_retry)
    elif state.notice:
        status_slot.info(state.notice)


def _frame(items: list[dict[str, Any]]) -> Any:
    rows = [
        {
            "時間": format_timestamp(item["occurred_at"]),
            "事件類型": item["event_label"],
            "嚴重程度": item["severity_label"],
            "使用者": item["username"] or _NONE,
            "說明": item["summary"],
        }
        for item in items
    ]
    frame = pd.DataFrame(rows, columns=list(_COLUMNS))

    def severity_style(row: pd.Series) -> list[str]:
        style = _SEVERITY_STYLES.get(items[row.name]["severity"], "")
        return [style if column == "嚴重程度" else "" for column in row.index]

    return frame.style.apply(severity_style, axis=1)


def _draw_table(state: _State, *, selectable: bool) -> dict[str, Any] | None:
    """Column selection is on so that header clicks cannot sort this page (server-side order)."""
    assert state.page is not None
    items = state.page["items"]
    common: dict[str, Any] = {"hide_index": True, "column_config": _COLUMNS, "placeholder": "", "width": "stretch"}
    if not selectable:
        st.dataframe(_frame(items), **common)
        return None
    event = st.dataframe(
        _frame(items),
        key=f"admin_logs_table_{state.generation}",
        on_select="rerun",
        selection_mode=["single-row", "single-column"],
        alt="系統日誌表格（唯讀），由新到舊；選取一列以查看詳細資料",
        **common,
    )
    rows = event.selection.rows
    return items[rows[0]] if rows and rows[0] < len(items) else None


def _range_caption(state: _State) -> str:
    assert state.query is not None and state.page is not None
    query = state.query
    end = f"{_minute(query['end'])}（查詢當時）" if query["end_is_now"] else _minute(query["end"])
    return f"查詢範圍：{_minute(query['start'])} – {end}；共 {state.page['total']:,} 筆，由新到舊；時間以 {display_zone().key} 顯示"


def _draw_pager(state: _State, *, waiting: bool) -> None:
    assert state.page is not None
    total = max(math.ceil(state.page["total"] / state.page["limit"]), len(state.stack), 1)
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        st.button("上一頁", key="admin_logs_previous", disabled=waiting or len(state.stack) <= 1, on_click=_previous_page)
        st.text(f"第 {len(state.stack)} / {total} 頁", width="content")
        st.button("下一頁", key="admin_logs_next", disabled=waiting or not state.page["next_cursor"], on_click=_next_page)


def _draw_details(item: dict[str, Any] | None) -> None:
    with st.container(border=True):
        if item is None:
            st.text("選取一列以查看詳細資料")
            return
        st.subheader("詳細資料", anchor=False)
        account = item["username"] or _NONE
        if item["user_id"] is not None:
            account = f"{account}（使用者編號 {item['user_id']}）"
        # st.text, not Markdown: the account name and summary are data.
        st.text(f"時間：{format_timestamp(item['occurred_at'])}")
        st.text(f"事件類型：{item['event_label']}（{item['event_name']}）")
        st.text(f"嚴重程度：{item['severity_label']}")
        st.text(f"使用者：{account}")
        st.text(f"說明：{item['summary']}")
        st.text(f"日誌編號：{item['id']}")
        if item["details"]:
            st.caption("補充資料")
            st.json(item["details"], expanded=True)
        else:
            st.caption("沒有補充資料")


def _draw_empty() -> None:
    with st.container(border=True, horizontal_alignment="center"):
        st.subheader(_EMPTY, anchor=False, text_alignment="center")
        st.text("試著放寬時間範圍或其他條件。", text_alignment="center")


def _draw_not_queried() -> None:
    with st.container(border=True, horizontal_alignment="center"):
        st.subheader("尚未查詢", anchor=False, text_alignment="center")
        st.text("修正條件後按「查詢」。", text_alignment="center")


def _draw_skeleton() -> None:
    with st.container(border=True):
        for _ in range(_SKELETON_ROWS):
            st.skeleton(height=_SKELETON_ROW_HEIGHT)


def _draw_results(state: _State) -> None:
    waiting = state.pending is not None
    if state.page is None:
        if waiting:
            _draw_skeleton()
        else:
            _draw_not_queried()
        return
    if not state.page["items"]:
        st.caption(_range_caption(state))
        _draw_empty()
        return
    selected = _draw_table(state, selectable=not waiting)
    st.caption(_range_caption(state))
    _draw_pager(state, waiting=waiting)
    if not waiting:
        _draw_details(selected)


def _rerun_list() -> None:
    """Redraw from the loaded state. A fragment rerun stays one, so it skips the shell's login check."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:  # Raised during a full run, where only a full rerun is allowed.
        st.rerun()


@st.fragment
def _logs_list() -> None:
    state = _state()
    _sync_widgets(state)
    _draw_form(state)
    _draw_messages(state)
    _draw_results(state)
    if state.pending is not None:
        _run_pending(state)
        _rerun_list()


def _description(state: _State) -> str:
    options = state.options or {}
    days = options.get("retention_days")
    months = _max_months(state)
    if days is None:
        return f"日誌唯讀；單次查詢最長 {months} 個月。"
    kept = "1 年" if float(days) == 365 else f"{float(days):g} 天"
    return f"日誌唯讀，保留 {kept}，超過的自動清理；單次查詢最長 {months} 個月。"


_page_state = _state()
_load_options(_page_state)
st.title("系統日誌", anchor=False)
st.caption(_description(_page_state))
_logs_list()
