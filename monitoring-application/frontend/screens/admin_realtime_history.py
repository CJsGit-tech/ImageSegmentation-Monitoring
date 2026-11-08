"""SCREEN-43: Admin traces raw system-generated (simulator) records by time, category and anomaly (PRD-16).

The list is one fragment, like SCREEN-10 and SCREEN-41: querying and paging do not re-run the
shell's login check. The first visit queries the past hour. An empty 結束時間 means 「現在」,
resolved again at each 查詢, so new readings show up after querying again; paging keeps the
resolved end, because the backend's cursor belongs to the exact conditions and order.

A range over 3 months is refused before sending, with the backend's rule (core/time_range.py):
the same wall clock 3 calendar months later in start's offset, so exactly 3 months is allowed.
Start may equal end (both inclusive), as the backend allows.

Read only and without export (PRD-16-R4, R8): no row selection, edit, delete or export control.
The table is st.table, because st.dataframe's toolbar downloads CSV. Only system records are
listed (R6); the backend enforces it. Titles, categories and units are data: escaped for
st.table's Markdown cells, and shown elsewhere through st.text.
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
from core.records import (
    display_zone,
    escape_markdown,
    format_timestamp,
    format_value,
    from_widget_time,
    parse_timestamp,
    range_position,
    to_widget_time,
)

PAGE_SIZE = 25
_HISTORY_PATH = "/api/v1/history/records"
_CATEGORIES_PATH = "/api/v1/records/categories"
_DISPLAY_PATH = "/api/v1/category-display"
_RECORDS_PAGE = "screens/records.py"
_DEFAULT_HOURS = 1
_MAX_MONTHS = 3  # The backend's MAX_RANGE_MONTHS.
_SKELETON_ROWS = 6
_SKELETON_ROW_HEIGHT = 32
_FIELD_TIME = 215
_FIELD_CHOICE = 200
_FIELD_ORDER = 170

_SCOPE_NOTE = "此頁只顯示系統產生的記錄；手動新增與導入的記錄請至資料列表。"
_LOAD_ERROR = "無法載入資料，已保留你的條件。"
_START_MISSING = "請選擇開始時間。"
_REVERSED = "開始時間不可晚於結束時間。"
_TOO_LONG = "時間範圍超過 3 個月上限，請縮短範圍。"
_EMPTY = "這段時間沒有系統產生的記錄"
_ANOMALY_LABEL = "◆ 異常 🔒"  # SCREEN-10's marker; the lock: the record itself is read-only.
_NO_RANGE_LABEL = "未設定範圍"
_ANOMALY_ROW_STYLE = "background-color: #FBE3E0"
_ANOMALY_STATUS_STYLE = "background-color: #FBE3E0; color: #A61B12; font-weight: 700"
_MIN_WIDTHS = {"狀態": "5.5em", "標題": "9em", "分類": "5em", "數值": "6em", "範圍": "8em", "時間戳": "10.5em", "創建者": "3.5em"}
_ORDER_LABELS = {"desc": "新到舊", "asc": "舊到新"}

# Under the prefix forms.reset_interrupted_submits() clears: changing page resets the screen.
_STATE_KEY = "__submit__admin_realtime_history"
_START = "admin_history_start"
_END = "admin_history_end"
_CATEGORIES = "admin_history_categories"
_ANOMALY_ONLY = "admin_history_anomaly_only"
_ORDER = "admin_history_order"

_COLUMNS = ["狀態", "標題", "分類", "數值", "範圍", "時間戳", "創建者"]


# --- Conditions ---


def _query(start: datetime, end: datetime, end_is_now: bool, categories: Any, anomaly_only: bool, order: str) -> dict[str, Any]:
    """A plain dict, not a dataclass: the page script redefines its classes on every rerun."""
    return {
        "start": start,
        "end": end,
        "end_is_now": end_is_now,
        "categories": tuple(categories or ()),
        "anomaly_only": bool(anomaly_only),
        "order": order if order in _ORDER_LABELS else "desc",
    }


def _params(query: dict[str, Any], cursor: str | None) -> dict[str, Any]:
    """requests repeats list values: ?category=a&category=b."""
    params: dict[str, Any] = {
        "start": query["start"].isoformat(),
        "end": query["end"].isoformat(),
        "order": query["order"],
        "limit": PAGE_SIZE,
    }
    if query["categories"]:
        params["category"] = list(query["categories"])
    if query["anomaly_only"]:
        params["anomaly_only"] = "true"
    if cursor:
        params["cursor"] = cursor
    return params


def _now_end() -> datetime:
    """「現在」: the next whole minute, so readings saved earlier in the current minute are included."""
    return datetime.now(display_zone()).replace(second=0, microsecond=0) + timedelta(minutes=1)


def _default_query() -> dict[str, Any]:
    now = datetime.now(display_zone()).replace(second=0, microsecond=0)
    return _query(now - timedelta(hours=_DEFAULT_HOURS), _now_end(), True, (), False, "desc")


def _minute(value: datetime) -> str:
    return value.astimezone(display_zone()).strftime("%Y-%m-%d %H:%M")


def _add_months(value: datetime, months: int) -> datetime:
    """The backend's rule (core/time_range.py): same wall clock in the value's own offset, the day
    clamped to the month's end."""
    index = value.month - 1 + months
    year, month = value.year + index // 12, index % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def _latest_allowed_end(start: datetime) -> datetime:
    # A fixed offset, as the backend uses: a zone with DST would move the wall clock by an hour.
    offset = start.utcoffset() or timedelta(0)
    return _add_months(start.astimezone(timezone(offset)), _MAX_MONTHS)


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
    categories: list[str] = field(default_factory=list)
    display: dict[str, dict[str, Any]] | None = None


def _state() -> _State:
    return st.session_state.setdefault(_STATE_KEY, _State())


def _start(state: _State, request: _Request, notice: str | None = None) -> None:
    state.pending, state.failed, state.error, state.field_error, state.hint, state.notice = request, None, None, None, None, notice


def _refresh_options(state: _State) -> None:
    """Category choices and current units/ranges. A failure keeps what we have."""
    try:
        state.categories = api.get(_CATEGORIES_PATH)["items"]
    except (api.BackendUnavailable, api.ApiError):
        pass
    try:
        items = api.get(_DISPLAY_PATH)["items"]
        state.display = {item["category"]: item for item in items}
    except (api.BackendUnavailable, api.ApiError):
        pass


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
    if start > end:
        state.field_error = _REVERSED
        return
    latest = _latest_allowed_end(start)
    if end > latest:
        # Refused before sending (PRD-16-R2); the backend refuses it too. Earlier results stay.
        state.field_error = _TOO_LONG
        state.hint = f"從 {_minute(start)} 起，結束時間最晚為 {_minute(latest)}（剛好 {_MAX_MONTHS} 個月可以查詢）。"
        return
    query = _query(
        start,
        end,
        chosen_end is None,
        st.session_state.get(_CATEGORIES),
        st.session_state.get(_ANOMALY_ONLY),
        st.session_state.get(_ORDER) or "desc",
    )
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
            page = api.get(_HISTORY_PATH, params=_params(request.query, request.stack[-1]))
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
            # Rows on this page are gone (deleted by an Admin): step back a page.
            state.pending = replace(request, stack=request.stack[:-1])
            continue
        _refresh_options(state)
        state.query, state.stack, state.page, state.pending = request.query, request.stack, page, None


# --- Drawing ---


def _sync_widgets(state: _State) -> None:
    """Widgets lose their values while the page is not drawn (SCREEN-04): refill from the applied query."""
    query = state.pending.query if state.pending else state.query
    if query is None:
        return
    defaults = {
        _START: to_widget_time(query["start"]),
        _END: None if query["end_is_now"] else to_widget_time(query["end"]),
        _CATEGORIES: list(query["categories"]),
        _ANOMALY_ONLY: query["anomaly_only"],
        _ORDER: query["order"],
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _category_choices(state: _State) -> list[str]:
    """Categories in the data, plus anything still chosen."""
    choices = list(state.categories)
    applied = (state.query or {}).get("categories", ())
    for chosen in [*(st.session_state.get(_CATEGORIES) or []), *applied]:
        if chosen not in choices:
            choices.append(chosen)
    return choices


def _draw_form(state: _State) -> None:
    with st.form("admin_history_filter", border=True):
        # Fixed widths in a wrapping row: fields keep a usable size on narrow screens.
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.datetime_input("開始時間（必填）", value=None, key=_START, step=60, format="YYYY-MM-DD", width=_FIELD_TIME)
            st.datetime_input(
                "結束時間",
                value=None,
                key=_END,
                step=60,
                format="YYYY-MM-DD",
                width=_FIELD_TIME,
                help="留空表示「現在」，每次查詢時重新取得目前時間。",
            )
            st.multiselect("分類", _category_choices(state), key=_CATEGORIES, placeholder="（全部）", width=_FIELD_CHOICE)
            st.selectbox("排序（時間戳）", list(_ORDER_LABELS), key=_ORDER, format_func=_ORDER_LABELS.get, width=_FIELD_ORDER)
        with st.container(horizontal=True, vertical_alignment="center"):
            st.toggle("僅顯示異常", key=_ANOMALY_ONLY)
            # Stays clickable while waiting: a newer query replaces the older one (M5 section 3).
            st.form_submit_button(
                "查詢中…" if state.pending and state.page else "查詢",
                key="admin_history_query",
                type="primary",
                on_click=_submit,
            )
        st.caption(
            f"時間以 {display_zone().key} 顯示與輸入，開始與結束都包含；結束時間留空表示「現在」；單次查詢最長 {_MAX_MONTHS} 個月。"
        )


def _draw_messages(state: _State) -> None:
    """Fixed slots, always in this order, so no message leaves a stale control behind."""
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
                st.button("重試", key="admin_history_retry", on_click=_retry)
    elif state.notice:
        status_slot.info(state.notice)


def _frame(state: _State) -> Any:
    assert state.page is not None
    items = state.page["items"]
    rows = []
    for record in items:
        display = (state.display or {}).get(record["category"])
        bounds = display["range"] if display else None
        if state.display is None:
            position = ""
        elif bounds:
            position = range_position(record["value"], bounds)
        else:
            position = _NO_RANGE_LABEL
        cells = {
            "狀態": _ANOMALY_LABEL if record["is_anomaly"] else "",
            "標題": record["title"],
            "分類": record["category"],
            "數值": format_value(record["value"], display["unit"] if display else None),
            "範圍": position,
            "時間戳": format_timestamp(record["timestamp"]),
            "創建者": record["creator_name"],
        }
        # st.table renders Markdown: titles, categories and units are user text.
        rows.append({name: escape_markdown(text) for name, text in cells.items()})
    frame = pd.DataFrame(rows, columns=_COLUMNS)

    def row_style(row: pd.Series) -> list[str]:
        # Minimum column widths: on a narrow screen the table scrolls sideways instead of
        # wrapping one character per line. Long titles still wrap past their minimum.
        anomaly = items[row.name]["is_anomaly"]
        styles = []
        for column in row.index:
            style = f"min-width: {_MIN_WIDTHS[column]}"
            if anomaly:
                style += f"; {_ANOMALY_STATUS_STYLE if column == '狀態' else _ANOMALY_ROW_STYLE}"
            styles.append(style)
        return styles

    return frame.style.apply(row_style, axis=1)


def _draw_table(state: _State) -> None:
    """st.table, not st.dataframe: the grid's toolbar has a CSV download and copy, and this page
    has no export (PRD-16-R8). st.table has no toolbar, selection or header sorting, so the order
    stays the server's. It parses cells as Markdown, so _frame escapes every text cell."""
    st.table(_frame(state), hide_index=True, alt="即時資料歷史表格（唯讀），異常列的狀態欄標示「異常」")


def _range_caption(state: _State) -> str:
    assert state.query is not None and state.page is not None
    query = state.query
    end = f"{_minute(query['end'])}（查詢當時）" if query["end_is_now"] else _minute(query["end"])
    order = _ORDER_LABELS[query["order"]]
    return f"查詢範圍：{_minute(query['start'])} – {end}；共 {state.page['total']:,} 筆，{order}；時間以 {display_zone().key} 顯示"


def _draw_pager(state: _State, *, waiting: bool) -> None:
    assert state.page is not None
    total = max(math.ceil(state.page["total"] / state.page["limit"]), len(state.stack), 1)
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        st.button("上一頁", key="admin_history_previous", disabled=waiting or len(state.stack) <= 1, on_click=_previous_page)
        st.text(f"第 {len(state.stack)} / {total} 頁", width="content")
        st.button("下一頁", key="admin_history_next", disabled=waiting or not state.page["next_cursor"], on_click=_next_page)


def _conditions_text(query: dict[str, Any]) -> str:
    parts = []
    if query["categories"]:
        parts.append("分類：" + "、".join(query["categories"]))
    if query["anomaly_only"]:
        parts.append("僅顯示異常")
    return "；".join(parts)


def _draw_empty(state: _State) -> None:
    assert state.query is not None
    with st.container(border=True, horizontal_alignment="center"):
        st.subheader(_EMPTY, anchor=False, text_alignment="center")
        conditions = _conditions_text(state.query)
        if conditions:
            # st.text: category names are data.
            st.text(f"條件：{conditions}", text_alignment="center")
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
        _draw_empty(state)
        return
    _draw_table(state)
    st.caption(_range_caption(state))
    _draw_pager(state, waiting=waiting)


def _rerun_list() -> None:
    """Redraw from the loaded state. A fragment rerun stays one, so it skips the shell's login check."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:  # Raised during a full run, where only a full rerun is allowed.
        st.rerun()


@st.fragment
def _history_list() -> None:
    state = _state()
    _sync_widgets(state)
    _draw_form(state)
    _draw_messages(state)
    _draw_results(state)
    if state.pending is not None:
        _run_pending(state)
        _rerun_list()


st.title("即時資料歷史", anchor=False)
with st.container(border=True, horizontal=True, vertical_alignment="center"):
    st.text(_SCOPE_NOTE, width="content")
    st.page_link(_RECORDS_PAGE, label="前往資料列表", width="content")
_history_list()
st.caption("這一頁唯讀，沒有匯出。")
