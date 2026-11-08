"""SCREEN-30: data analysis. A condition bar, the tabs 統計與趨勢 and 異常記錄, and an Excel download.

The conditions are plain widgets, not a form: SCREEN-30 has to say 「條件已變更，尚未查詢」 as soon
as a field changes, which a form would hold back until submit. Changing a field reruns the page
without calling the backend; only 查詢, paging, sorting and the download call it. The last
successful query (`shown`) is what the tabs show and what the download uses (PRD-12-R2).

The download has two steps. 「下載 Excel」 asks the backend for the file (up to about 28 s and
22 MB for 3 months), with the conditions and 查詢 disabled meanwhile; 「儲存檔案」 then hands the
bytes to the browser. The bytes live in this session only until the conditions change, another
query is shown or the page changes. A callable `data=` would give one click, but it could not
show the waiting state, the backend's refusal or 「已下載」.
"""

import calendar
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st

from core import api, charts, records

# Under the prefix forms.reset_interrupted_submits() clears: changing page resets the screen.
_STATE_KEY = "__submit__analysis"
_START = "analysis_start"
_END = "analysis_end"
_CATEGORIES = "analysis_categories"
_GRANULARITY = "analysis_granularity"
_SORT_BY = "analysis_sort_by"
_ORDER = "analysis_order"
_TABS = "analysis_tabs"
_CONDITION_KEYS = (_START, _END, _CATEGORIES, _GRANULARITY, _SORT_BY, _ORDER)

_STATISTICS_PATH = "/api/v1/analytics/statistics"
_TREND_PATH = "/api/v1/analytics/trend"
_ANOMALIES_PATH = "/api/v1/analytics/anomalies"
_EXPORT_PATH = "/api/v1/analytics/export"
_EXPORT_TIMEOUT = (5, 120)
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAX_MONTHS = 3
_DEFAULT_DAYS = 7

_GRANULARITIES = {"minute": "每分鐘", "hour": "每小時", "day": "每天"}
_SORT_LABELS = {"timestamp": "時間戳", "title": "標題", "value": "數值", "category": "分類"}
_ORDER_LABELS = {"desc": "遞減（新到舊）", "asc": "遞增（舊到新）"}
_TAB_LABELS = ["統計與趨勢", "異常記錄"]
_EMPTY = "—"

_LOAD_ERROR = "無法載入資料，已保留你的條件。"
_TOO_LONG = "時間範圍超過 3 個月上限。要看更長的期間，請分批查詢（一次一段）。"
_ANOMALY_LABEL = "◆ 異常 🔒"
_ANOMALY_ROW_STYLE = "background-color: #FBE3E0"
_ANOMALY_STATUS_STYLE = "background-color: #FBE3E0; color: #A61B12; font-weight: 700"
_ANOMALY_COLUMNS = {
    "狀態": st.column_config.TextColumn("狀態", width=90),
    "標題": st.column_config.TextColumn("標題", width="medium"),
    "分類": st.column_config.TextColumn("分類", width="small"),
    "數值": st.column_config.TextColumn("數值", width="small"),
    "範圍": st.column_config.TextColumn("範圍", width=190, help="數值在此分類目前範圍中的位置"),
    "時間戳": st.column_config.TextColumn("時間戳", width="medium"),
    "創建者": st.column_config.TextColumn("創建者", width="small"),
}
_STAT_COLUMNS = {
    name: st.column_config.TextColumn(name, width="medium" if name == "分類" else "small")
    for name in ("分類", "總計", "平均", "最大值", "最小值")
}


# --- Conditions ---


@dataclass(frozen=True)
class Conditions:
    start: datetime
    end: datetime
    categories: tuple[str, ...]
    granularity: str

    def params(self) -> dict[str, Any]:
        """Statistics, anomaly list and export parameters. requests repeats `category` and encodes `+`."""
        params: dict[str, Any] = {"start": self.start.isoformat(), "end": self.end.isoformat()}
        if self.categories:
            params["category"] = list(self.categories)
        return params

    def trend_params(self) -> dict[str, Any]:
        return {**self.params(), "granularity": self.granularity}

    def without_granularity(self) -> tuple[datetime, datetime, tuple[str, ...]]:
        return self.start, self.end, self.categories

    def text(self) -> str:
        categories = "、".join(self.categories) if self.categories else "全部"
        return f"{_minute(self.start)} – {_minute(self.end)} · 分類：{categories}"


def _same(a: Conditions | None, b: Conditions | None) -> bool:
    """Compare by fields: the page script runs again on every rerun and redefines Conditions, so the
    dataclass __eq__ would treat a value saved in an earlier run as different."""
    if a is None or b is None:
        return a is b
    return (a.start, a.end, a.categories, a.granularity) == (b.start, b.end, b.categories, b.granularity)


def _minute(value: datetime) -> str:
    return value.astimezone(records.display_zone()).strftime("%Y-%m-%d %H:%M")


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


def _current() -> Conditions | None:
    """The conditions on screen, or None while a time is missing."""
    start = records.from_widget_time(st.session_state.get(_START))
    end = records.from_widget_time(st.session_state.get(_END))
    if start is None or end is None:
        return None
    return Conditions(start, end, tuple(st.session_state.get(_CATEGORIES) or ()), st.session_state.get(_GRANULARITY) or "hour")


# --- State ---


@dataclass
class AnalysisState:
    pending: Conditions | None = None
    pending_trend_only: bool = False
    shown: Conditions | None = None
    statistics: dict[str, Any] | None = None
    trend: dict[str, Any] | None = None
    trend_error: dict[str, Any] | None = None
    failed: tuple[Conditions, bool] | None = None
    error: str | None = None
    field_error: str | None = None
    hint: str | None = None
    # The anomaly list: its own page, cursor stack and sort, always on `shown`'s conditions.
    anomalies: dict[str, Any] | None = None
    stack: tuple[str | None, ...] = (None,)
    sort: tuple[str, str] = ("timestamp", "desc")
    anomaly_pending: tuple[tuple[str | None, ...], tuple[str, str]] | None = None
    anomaly_failed: tuple[tuple[str | None, ...], tuple[str, str]] | None = None
    anomaly_error: str | None = None
    anomaly_notice: str | None = None
    # The download: idle, requested, running, ready, downloaded or failed.
    export: str = "idle"
    export_for: Conditions | None = None
    export_bytes: bytes | None = None
    export_name: str | None = None
    export_error: str | None = None
    snapshot: dict[str, Any] | None = None
    categories: list[str] | None = None
    display: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def exporting(self) -> bool:
        return self.export in ("requested", "running")


def _state() -> AnalysisState:
    return st.session_state.setdefault(_STATE_KEY, AnalysisState())


def _drop_export(state: AnalysisState) -> None:
    """Frees the file: it only matches the result it was made for."""
    state.export, state.export_for, state.export_bytes, state.export_name, state.export_error = "idle", None, None, None, None


def _refresh_options(state: AnalysisState) -> None:
    """Category choices and units. A failure keeps what we have."""
    try:
        state.categories = api.get("/api/v1/records/categories")["items"]
    except (api.BackendUnavailable, api.ApiError):
        state.categories = state.categories or []
    try:
        state.display = {item["category"]: item for item in api.get("/api/v1/category-display")["items"]}
    except (api.BackendUnavailable, api.ApiError):
        pass


def _prepare_widgets(state: AnalysisState) -> None:
    """Defaults, and the values the widgets lose while disabled or while the page was not drawn."""
    if state.snapshot is not None:
        st.session_state.update(state.snapshot)
        if not state.exporting:
            state.snapshot = None
    shown = state.shown
    if shown is not None:
        defaults: dict[str, Any] = {
            _START: records.to_widget_time(shown.start),
            _END: records.to_widget_time(shown.end),
            _CATEGORIES: list(shown.categories),
            _GRANULARITY: shown.granularity,
        }
    else:
        now = datetime.now(records.display_zone()).replace(second=0, microsecond=0)
        start = (now - timedelta(days=_DEFAULT_DAYS)).replace(hour=0, minute=0)
        # The widgets hold whole minutes and the end is inclusive at :00, so the next minute is the
        # first default that still includes records saved earlier in the current minute.
        end = now + timedelta(minutes=1)
        defaults = {_START: records.to_widget_time(start), _END: records.to_widget_time(end), _CATEGORIES: [], _GRANULARITY: "hour"}
    defaults[_SORT_BY], defaults[_ORDER] = state.sort
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# --- Actions (button callbacks) ---


def _submit() -> None:
    state = _state()
    state.error = state.failed = state.field_error = state.hint = None
    current = _current()
    if current is None:
        state.field_error = "請選擇開始與結束時間。"
        return
    if current.start > current.end:
        state.field_error = "開始時間不能晚於結束時間。"
        return
    latest = _latest_allowed_end(current.start)
    if current.end > latest:
        # Refused before sending (PRD-10-R8); the backend refuses it too.
        state.field_error = _TOO_LONG
        state.hint = f"從 {_minute(current.start)} 起，結束時間最晚為 {_minute(latest)}（剛好 3 個月可以查詢）。"
        return
    shown = state.shown
    trend_only = shown is not None and state.statistics is not None and shown.without_granularity() == current.without_granularity()
    state.pending, state.pending_trend_only = current, trend_only


def _retry() -> None:
    state = _state()
    if state.failed:
        (state.pending, state.pending_trend_only), state.failed, state.error = state.failed, None, None


def _sort_changed() -> None:
    state = _state()
    if state.shown is not None:
        state.anomaly_pending = ((None,), (st.session_state[_SORT_BY], st.session_state[_ORDER]))
        state.anomaly_error = state.anomaly_notice = None
    else:
        state.sort = (st.session_state[_SORT_BY], st.session_state[_ORDER])


def _previous_page() -> None:
    state = _state()
    if len(state.stack) > 1:
        state.anomaly_pending, state.anomaly_error, state.anomaly_notice = (state.stack[:-1], state.sort), None, None


def _next_page() -> None:
    state = _state()
    cursor = state.anomalies["next_cursor"] if state.anomalies else None
    if cursor:
        state.anomaly_pending, state.anomaly_error, state.anomaly_notice = ((*state.stack, cursor), state.sort), None, None


def _retry_anomalies() -> None:
    state = _state()
    if state.anomaly_failed:
        state.anomaly_pending, state.anomaly_failed, state.anomaly_error = state.anomaly_failed, None, None


def _begin_export() -> None:
    state = _state()
    _drop_export(state)
    state.export, state.export_for = "requested", state.shown
    # Disabled widgets lose their values in Streamlit 1.67: put them back on every run.
    state.snapshot = {key: st.session_state.get(key) for key in _CONDITION_KEYS}


def _mark_downloaded() -> None:
    _state().export = "downloaded"


# --- Backend calls (run after the waiting view is drawn) ---


def _anomaly_params(conditions: Conditions, stack: tuple[str | None, ...], sort: tuple[str, str]) -> dict[str, Any]:
    params = {**conditions.params(), "sort_by": sort[0], "order": sort[1], "limit": records.PAGE_SIZE}
    if stack[-1]:
        params["cursor"] = stack[-1]
    return params


def _query_error(state: AnalysisState, request: Conditions, trend_only: bool, exc: api.ApiError) -> None:
    if exc.code == "RANGE_TOO_LONG":
        state.field_error = _TOO_LONG
        latest = exc.details.get("latest_allowed_end") if isinstance(exc.details, dict) else None
        state.hint = f"結束時間最晚為 {_minute(records.parse_timestamp(latest))}。" if latest else None
    elif exc.status == 422 and exc.field_errors():
        state.field_error = next(iter(exc.field_errors().values()))
    elif exc.status >= 500:
        state.failed, state.error = (request, trend_only), _LOAD_ERROR
    else:
        state.error = exc.message


def _run_query(state: AnalysisState) -> None:
    request, trend_only = state.pending, state.pending_trend_only
    assert request is not None
    sort = (st.session_state.get(_SORT_BY) or "timestamp", st.session_state.get(_ORDER) or "desc")
    try:
        statistics = state.statistics if trend_only else api.get(_STATISTICS_PATH, params=request.params())
        trend, trend_error = None, None
        try:
            trend = api.get(_TREND_PATH, params=request.trend_params())
        except api.ApiError as exc:
            if exc.code != "TOO_MANY_POINTS":
                raise
            trend_error = exc.details if isinstance(exc.details, dict) else {}
            trend_error.setdefault("granularity", request.granularity)
        anomalies = None if trend_only else api.get(_ANOMALIES_PATH, params=_anomaly_params(request, (None,), sort))
    except api.BackendUnavailable:
        state.pending, state.failed, state.error = None, (request, trend_only), _LOAD_ERROR
        return
    except api.ApiError as exc:
        state.pending = None
        _query_error(state, request, trend_only, exc)
        return
    if not trend_only:
        state.statistics, state.anomalies, state.stack, state.sort = statistics, anomalies, (None,), sort
        state.anomaly_pending = state.anomaly_failed = state.anomaly_error = state.anomaly_notice = None
        _refresh_options(state)
    state.trend, state.trend_error = trend, trend_error
    state.shown, state.pending = request, None
    _drop_export(state)


def _run_anomalies(state: AnalysisState) -> None:
    assert state.anomaly_pending is not None and state.shown is not None
    stack, sort = state.anomaly_pending
    try:
        page = api.get(_ANOMALIES_PATH, params=_anomaly_params(state.shown, stack, sort))
    except api.BackendUnavailable:
        state.anomaly_pending, state.anomaly_failed, state.anomaly_error = None, (stack, sort), _LOAD_ERROR
        return
    except api.ApiError as exc:
        fields = exc.field_errors()
        if exc.status == 422 and "cursor" in fields:
            state.anomaly_pending, state.anomaly_notice = ((None,), sort), fields["cursor"]
            return
        state.anomaly_pending = None
        state.anomaly_failed = (stack, sort) if exc.status >= 500 else None
        state.anomaly_error = _LOAD_ERROR if exc.status >= 500 else exc.message
        return
    state.anomalies, state.stack, state.sort, state.anomaly_pending = page, stack, sort, None


def _run_export(state: AnalysisState) -> None:
    if state.export == "running":
        # A rerun interrupted the download; it is not repeated by itself.
        state.export, state.export_error = "failed", "下載已中斷，請再試一次。"
        return
    state.export = "running"
    assert state.export_for is not None
    try:
        download = api.get_bytes(_EXPORT_PATH, params=state.export_for.params(), timeout=_EXPORT_TIMEOUT)
    except api.BackendTimeout:
        state.export, state.export_error = "failed", "產生檔案逾時。請縮短時間範圍或減少分類後再試一次。"
        return
    except api.BackendUnavailable:
        state.export, state.export_error = "failed", "無法連到系統，請稍後再試一次。"
        return
    except api.ApiError as exc:
        state.export, state.export_error = "failed", exc.message
        return
    state.export, state.export_bytes = "ready", download.content
    state.export_name = download.filename or "analysis.xlsx"


# --- Drawing ---


def _number(value: float | None) -> str:
    if value is None:
        return _EMPTY
    text = f"{value:,.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _empty_state(title: str, text: str | None = None) -> None:
    with st.container(border=True, horizontal_alignment="center"):
        st.markdown(f"**{title}**", text_alignment="center")
        if text:
            st.text(text, text_alignment="center")


def _download_reason(state: AnalysisState, changed: bool) -> str | None:
    """Why 下載 Excel is disabled (SHARED-04: as text), or None when it is enabled."""
    if state.exporting:
        return "正在產生 Excel…"
    if state.pending is not None:
        return "查詢中"
    if state.field_error and (state.shown is None or changed):
        return "條件有誤"
    if state.shown is None:
        return "尚未查詢"
    if changed:
        return "條件已變更，請先查詢"
    if state.statistics is None or not state.statistics["has_data"]:
        return "沒有可下載的內容"
    return None


def _draw_bar(state: AnalysisState, changed: bool) -> None:
    busy = state.exporting
    options = list(state.categories or [])
    for chosen in [*(st.session_state.get(_CATEGORIES) or []), *(state.shown.categories if state.shown else ())]:
        if chosen not in options:
            options.append(chosen)
    reason = _download_reason(state, changed)
    with st.container(border=True):
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="bottom"):
            # Fixed widths in a wrapping row: fields keep a usable size on narrow screens.
            with st.container(horizontal=True, vertical_alignment="bottom", width="stretch"):
                st.datetime_input("開始時間", key=_START, step=60, format="YYYY-MM-DD", width=200, disabled=busy)
                st.datetime_input("結束時間", key=_END, step=60, format="YYYY-MM-DD", width=200, disabled=busy)
                st.multiselect("分類", options, key=_CATEGORIES, placeholder="全部分類", width=280, disabled=busy)
                st.segmented_control(
                    "趨勢粒度", list(_GRANULARITIES), key=_GRANULARITY, format_func=_GRANULARITIES.get, required=True, disabled=busy
                )
                # Stays clickable while a query runs: a newer query replaces the older one.
                st.button("查詢中…" if state.pending else "查詢", key="analysis_query", type="primary", disabled=busy, on_click=_submit)
            with st.container(width="content"):
                if state.export in ("ready", "downloaded") and state.export_bytes is not None:
                    st.download_button(
                        "儲存檔案",
                        data=state.export_bytes,
                        file_name=state.export_name,
                        mime=_XLSX,
                        key="analysis_save",
                        type="primary",
                        icon=":material/download:",
                        on_click=_mark_downloaded,
                    )
                else:
                    st.button(
                        "產生檔案中…" if busy else "下載 Excel",
                        key="analysis_download",
                        type="primary",
                        disabled=reason is not None,
                        icon=":material/progress_activity:" if busy else None,
                        on_click=_begin_export,
                    )
        field_slot = st.empty()
        if state.field_error:
            with field_slot.container():
                st.error(state.field_error)
                if state.hint:
                    st.caption(state.hint)
        st.caption(
            f"時間以 {records.display_zone().key} 顯示與輸入，開始與結束都包含；單次最長 3 個月。"
            + (f"　下載停用：{reason}" if reason else "")
        )


def _draw_messages(state: AnalysisState, changed: bool) -> None:
    """Fixed slots, always in this order, so no message leaves a stale control behind."""
    export_slot = st.empty()
    if state.export == "ready" and state.export_for:
        export_slot.info(f"檔案已準備好：{records.escape_markdown(state.export_name or '')}（條件：{records.escape_markdown(state.export_for.text())}）。按「儲存檔案」取得檔案。")
    elif state.export == "downloaded" and state.export_for:
        export_slot.success(f"已下載 {records.escape_markdown(state.export_name or '')}（條件：{records.escape_markdown(state.export_for.text())}）。")
    elif state.export == "failed" and state.export_error:
        with export_slot.container(horizontal=True, vertical_alignment="center"):
            st.error(f"無法下載 Excel：{state.export_error}")
            st.button("重試", key="analysis_export_retry", on_click=_begin_export, disabled=state.shown is None or changed)
    elif state.exporting:
        export_slot.info("正在產生 Excel，資料量大時需要數十秒。完成前條件與查詢暫停使用。")
    status_slot = st.empty()
    if state.pending is not None:
        status_slot.info("查詢中…顯示的是舊結果。" if state.shown else "查詢中…")
    elif state.error:
        with status_slot.container(horizontal=True, vertical_alignment="center"):
            st.error(state.error)
            if state.failed:
                st.button("重試", key="analysis_retry", on_click=_retry)
    elif changed:
        status_slot.warning("條件已變更，尚未查詢。下方是**上一次查詢**的結果。")


def _wall_ms(value: datetime) -> int:
    """The viewer's wall-clock time as if it were UTC, for a chart on a UTC scale."""
    return calendar.timegm(value.astimezone(records.display_zone()).timetuple()) * 1000


def _trend_rows(series: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unit = series["unit"]
    rows, marks = [], []
    for point in series["points"]:
        start = records.parse_timestamp(point["bucket_start"])
        t, time = _wall_ms(start), records.format_timestamp(start)
        rows.append(
            {
                "t": t,
                "avg": float(point["avg"]),
                "min": float(point["min"]),
                "max": float(point["max"]),
                "time": time,
                "avg_text": records.format_value(round(float(point["avg"]), 3), unit),
                "spread_text": f"{records.format_value(float(point['min']), unit)} – {records.format_value(float(point['max']), unit)}",
                "status": "◆ 含異常" if point["anomaly_count"] else "正常",
            }
        )
        if point["anomaly_count"]:
            # The real anomalous values, not the average (PRD-11-R9); both ends when they differ.
            values = {point["anomaly_max"], point["anomaly_min"]} - {None}
            for value in values:
                marks.append({"t": t, "value": float(value), "time": time, "value_text": records.format_value(float(value), unit)})
    return rows, marks


def _draw_statistics(state: AnalysisState) -> None:
    assert state.statistics is not None
    rows = []
    for item in state.statistics["categories"]:
        rows.append(
            {
                # No count and no total row (PRD-10-R10, R12).
                "分類": f"{item['category']} {item['unit']}" if item["unit"] else item["category"],
                "總計": _number(item["sum"]),
                "平均": _number(item["avg"]),
                "最大值": _number(item["max"]),
                "最小值": _number(item["min"]),
            }
        )
    st.dataframe(
        pd.DataFrame(rows, columns=list(_STAT_COLUMNS)),
        hide_index=True,
        width="stretch",
        column_config=_STAT_COLUMNS,
        alt="各分類統計：總計、平均、最大值、最小值；沒有跨分類合計",
    )
    if any(item["sum"] is None for item in state.statistics["categories"]):
        st.caption(f"「{_EMPTY}」表示此分類在範圍內沒有資料。")
    _draw_anomaly_columns(state)


def _draw_anomaly_columns(state: AnalysisState) -> None:
    """A column per category: how many records in the range are flagged as anomalies."""
    assert state.statistics is not None
    rows = [{"name": item["category"], "count": item["anomaly_count"]} for item in state.statistics["categories"]]
    with st.container(border=True):
        st.markdown("**各分類異常次數（柱狀圖）**")
        st.altair_chart(charts.anomaly_count_bars(rows), width="stretch")
        if not any(row["count"] for row in rows):
            st.caption("這段條件內沒有異常記錄。")


def _draw_trend(state: AnalysisState) -> None:
    shown = state.shown
    assert shown is not None
    if state.trend_error is not None:
        chosen = _GRANULARITIES.get(state.trend_error.get("granularity"), "所選粒度")
        coarser = [_GRANULARITIES[name] for name in state.trend_error.get("coarser") or [] if name in _GRANULARITIES]
        advice = f"請改選較粗的粒度（{'或'.join(coarser)}）。" if coarser else "請改選較粗的粒度或縮短範圍。"
        st.warning(f"所選粒度（{chosen}）× 時間範圍會產生過多資料點，因此不產生趨勢圖。{advice}")
        return
    trend = state.trend
    assert trend is not None
    start_ms = _wall_ms(shown.start)
    end_ms = max(_wall_ms(shown.end), start_ms + trend["bucket_seconds"] * 1000)
    for series in trend["categories"]:
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="center"):
                name = records.escape_markdown(series["category"])
                unit = f" {records.escape_markdown(series['unit'])}" if series["unit"] else ""
                st.markdown(f"**{name}**{unit}", width="content")
                if series["range"] is None:
                    st.badge("未設定範圍", color="orange")
            if not series["points"]:
                st.caption("此分類在這段條件內沒有資料。")
                continue
            rows, marks = _trend_rows(series)
            chart = charts.trend_chart(rows, marks, charts.bounds_of(series["range"]), start_ms=start_ms, end_ms=end_ms, title=series["category"])
            st.altair_chart(chart, width="stretch")
            st.caption(f"{trend['label']}平均；含異常的區間仍以 ◆ 標記實際異常數值")


def _draw_tab_statistics(state: AnalysisState) -> None:
    if state.shown is None:
        if state.pending is not None:
            st.skeleton(height=170)
            for _ in range(3):
                st.skeleton(height=220)
        elif state.field_error:
            _empty_state("尚未查詢", "修正條件後按「查詢」。")
        else:
            _empty_state("設定條件後按「查詢」", "統計與趨勢會顯示在這裡。")
        return
    assert state.statistics is not None
    if not state.statistics["has_data"]:
        _empty_state("這段條件內沒有資料", f"{state.shown.text()}。這不是 0，而是沒有記錄。")
        return
    _draw_statistics(state)
    _draw_trend(state)
    st.caption(f"時間以 {records.display_zone().key} 顯示")


def _anomaly_frame(items: list[dict[str, Any]], display: dict[str, dict[str, Any]]) -> Any:
    rows = []
    for record in items:
        shown = display.get(record["category"])
        bounds = shown["range"] if shown else None
        rows.append(
            {
                "狀態": _ANOMALY_LABEL if record["is_anomaly"] else "",
                "標題": record["title"],
                "分類": record["category"],
                "數值": records.format_value(record["value"], shown["unit"] if shown else None),
                "範圍": records.range_position(record["value"], bounds) if bounds else "未設定範圍",
                "時間戳": records.format_timestamp(record["timestamp"]),
                "創建者": record["creator_name"],
            }
        )
    frame = pd.DataFrame(rows, columns=list(_ANOMALY_COLUMNS))

    def row_style(row: pd.Series) -> list[str]:
        if not items[row.name]["is_anomaly"]:
            return [""] * len(row)
        return [_ANOMALY_STATUS_STYLE if column == "狀態" else _ANOMALY_ROW_STYLE for column in row.index]

    return frame.style.apply(row_style, axis=1)


def _draw_tab_anomalies(state: AnalysisState) -> None:
    busy = state.exporting or state.shown is None
    with st.container(horizontal=True, vertical_alignment="bottom"):
        # Server-side paging: an explicit sort control, never header sorting (SHARED-03).
        st.selectbox("排序欄位", list(_SORT_LABELS), key=_SORT_BY, format_func=_SORT_LABELS.get, width=160, disabled=busy, on_change=_sort_changed)
        st.selectbox("排序方向", list(_ORDER_LABELS), key=_ORDER, format_func=_ORDER_LABELS.get, width=200, disabled=busy, on_change=_sort_changed)
    if state.shown is None:
        if state.pending is not None:
            for _ in range(6):
                st.skeleton(height=32)
        else:
            _empty_state("設定條件後按「查詢」", "異常記錄會顯示在這裡。")
        return
    message = st.empty()
    if state.anomaly_pending is not None:
        message.info("更新中…顯示的是舊結果。")
    elif state.anomaly_error:
        with message.container(horizontal=True, vertical_alignment="center"):
            st.error(state.anomaly_error)
            if state.anomaly_failed:
                st.button("重試", key="analysis_anomaly_retry", on_click=_retry_anomalies)
    elif state.anomaly_notice:
        message.info(state.anomaly_notice)
    page = state.anomalies
    assert page is not None
    if not page["items"]:
        _empty_state("這段條件內沒有異常記錄", state.shown.text())
        return
    st.dataframe(
        _anomaly_frame(page["items"], state.display),
        hide_index=True,
        column_config=_ANOMALY_COLUMNS,
        placeholder="",
        alt="異常記錄表格（唯讀），每列的狀態欄標示「異常」",
    )
    waiting = state.anomaly_pending is not None or state.exporting
    total = max(math.ceil(page["total"] / page["limit"]), len(state.stack), 1)
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        st.caption(f"共 {page['total']:,} 筆異常記錄；時間以 {records.display_zone().key} 顯示", width="stretch")
        st.button("上一頁", key="analysis_previous", disabled=waiting or len(state.stack) <= 1, on_click=_previous_page)
        st.text(f"第 {len(state.stack)} / {total} 頁", width="content")
        st.button("下一頁", key="analysis_next", disabled=waiting or not page["next_cursor"], on_click=_next_page)


def _page() -> None:
    state = _state()
    if state.categories is None:
        _refresh_options(state)
    _prepare_widgets(state)
    current = _current()
    changed = state.shown is not None and not _same(current, state.shown)
    # The file only matches the result it was made for: a change or a newer result frees it.
    if state.export_for is not None and not state.exporting and (changed or not _same(state.export_for, state.shown)):
        _drop_export(state)

    st.title("資料分析", anchor=False)
    _draw_bar(state, changed)
    _draw_messages(state, changed)
    tab_statistics, tab_anomalies = st.tabs(_TAB_LABELS, key=_TABS, on_change="rerun")
    # Only the open tab is drawn.
    if tab_statistics.open:
        with tab_statistics:
            _draw_tab_statistics(state)
    if tab_anomalies.open:
        with tab_anomalies:
            _draw_tab_anomalies(state)

    # Backend calls last, with the waiting view already on screen, then draw the result.
    if state.exporting:
        _run_export(state)
        st.rerun()
    if state.pending is not None:
        _run_query(state)
        st.rerun()
    if state.anomaly_pending is not None:
        _run_anomalies(state)
        st.rerun()


_page()
