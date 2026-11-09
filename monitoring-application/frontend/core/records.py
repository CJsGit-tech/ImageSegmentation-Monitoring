"""Data records: API helpers, time and value formatting, the action rule and the list's state.

Hook for SCREEN-12 and SCREEN-13 (LB-342). SCREEN-10 owns the selection and the action row.
It calls open_edit(record) or open_delete(record) when an enabled button is pressed; LB-342
replaces only those two functions. A record is the API's dict. Inside them, after a change:

    records.request_reload(notice="已刪除", keep_selected_id=None)   # filters, sort and page kept
    st.rerun()

On 404 RECORD_NOT_FOUND call records.record_gone(), then st.rerun().
"""

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from core import api, session

PAGE_SIZE = 25
LOAD_ERROR = "無法載入資料，已保留你的篩選條件。"
NOT_OWNER_REASON = "只有建立者或 Admin 可修改"
RECORD_GONE = "這筆記錄已不存在"

# Kept under the prefix forms.reset_interrupted_submits() clears, because that is the shell's
# only signal that the page changed (SHARED-01: changing page does not keep filters). The state
# has to survive reruns where the page does not run, such as SCREEN-04 while the backend is down.
_STATE_KEY = "__submit__records_list"
# The widget offers minutes, so an end of 14:03 has to include 14:03:21 as the screen shows it.
_END_OF_MINUTE = timedelta(seconds=59, microseconds=999999)
_RAIL = "├───────┤"
_MARK = "●"


# --- Time and value formatting ---


def display_zone() -> ZoneInfo:
    """The browser's timezone; UTC when the browser did not report one."""
    try:
        return ZoneInfo(st.context.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def parse_timestamp(text: str) -> datetime:
    """The API writes UTC with Z and omits a zero fraction, so compare parsed values, not text."""
    return datetime.fromisoformat(text)


def format_timestamp(value: datetime | str) -> str:
    parsed = parse_timestamp(value) if isinstance(value, str) else value
    return parsed.astimezone(display_zone()).strftime("%Y-%m-%d %H:%M:%S")


def to_widget_time(value: datetime | None) -> datetime | None:
    """st.datetime_input holds naive times: this is what the viewer's clock shows."""
    return value.astimezone(display_zone()).replace(tzinfo=None) if value else None


def from_widget_time(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=display_zone()) if value else None


def format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() and abs(value) < 1e15 else str(value)


def format_value(value: float, unit: str | None) -> str:
    return f"{format_number(value)} {unit}" if unit else format_number(value)


def range_position(value: float, bounds: dict[str, float]) -> str:
    """Where the value sits in its category's current range, as text. Outside shows outside the rail."""
    low, high = bounds["min"], bounds["max"]
    if value < low:
        return f"{_MARK}{_RAIL}"
    if value > high:
        return f"{_RAIL}{_MARK}"
    last = len(_RAIL) - 1
    if value == low or value == high:
        slot = 0 if value == low else last
    else:
        # Only the bounds themselves sit on the rail's ends: a bound counts as anomalous (PRD-09-R7).
        slot = min(max(round((value - low) / (high - low) * last), 1), last - 1)
    return f"{_RAIL[:slot]}{_MARK}{_RAIL[slot + 1:]}"


def escape_markdown(text: str) -> str:
    """Button labels parse Markdown, and titles and categories are user text."""
    return "".join(f"\\{char}" if char in r"\`*_{}[]()<>#+-.!|:$~&" else char for char in text)


# --- Which actions a user has ---


@dataclass(frozen=True)
class Availability:
    enabled: bool
    reason: str | None = None


def can_act_on_records(user: session.CurrentUser) -> bool:
    """False for Viewer, who gets no selection and no action row."""
    return "records.modify_own" in user.capabilities


def availability(user: session.CurrentUser, record: dict[str, Any]) -> Availability:
    """The screen's copy of the rule. The backend still enforces it."""
    if "records.modify_any" in user.capabilities:
        return Availability(True)
    # creator_id, never creator_name: a user may have registered the name 系統.
    if not record["is_system"] and record["creator_id"] == user.id:
        return Availability(True)
    return Availability(False, NOT_OWNER_REASON)


# --- The list's query and state ---


@dataclass(frozen=True)
class Query:
    title: str | None = None
    category: str | None = None
    value_min: float | None = None
    value_max: float | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None
    anomaly_only: bool = False
    sort_by: str = "timestamp"
    order: str = "desc"

    @property
    def has_filters(self) -> bool:
        return self != Query(sort_by=self.sort_by, order=self.order)

    def params(self, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"sort_by": self.sort_by, "order": self.order, "limit": PAGE_SIZE}
        for name in ("title", "category", "value_min", "value_max"):
            if getattr(self, name) is not None:
                params[name] = getattr(self, name)
        if self.time_from:
            params["time_from"] = self.time_from.isoformat()
        if self.time_to:
            params["time_to"] = (self.time_to + _END_OF_MINUTE).isoformat()
        if self.anomaly_only:
            params["anomaly_only"] = "true"
        if cursor:
            params["cursor"] = cursor
        return params


@dataclass(frozen=True)
class Request:
    """One page to load. It only becomes the current page when the backend answers."""

    query: Query
    stack: tuple[str | None, ...] = (None,)
    keep_selected_id: int | None = None


@dataclass
class ListState:
    query: Query = field(default_factory=Query)
    stack: tuple[str | None, ...] = (None,)
    page: dict[str, Any] | None = None
    pending: Request | None = field(default_factory=lambda: Request(Query()))
    failed: Request | None = None
    error: str | None = None
    field_errors: dict[str, str] = field(default_factory=dict)
    notice: str | None = None
    generation: int = 0
    selected_id: int | None = None
    categories: list[str] = field(default_factory=list)
    display: dict[str, dict[str, Any]] | None = None


def list_state() -> ListState:
    return st.session_state.setdefault(_STATE_KEY, ListState())


def _start(state: ListState, request: Request, notice: str | None = None) -> None:
    state.pending = request
    state.failed = None
    state.error = None
    state.field_errors = {}
    state.notice = notice


def request_query(query: Query) -> None:
    """Apply a new query: a changed filter or sort starts again at the first page."""
    _start(list_state(), Request(query))


def request_previous_page() -> None:
    state = list_state()
    if len(state.stack) > 1:
        _start(state, Request(state.query, state.stack[:-1]))


def request_next_page() -> None:
    state = list_state()
    next_cursor = state.page["next_cursor"] if state.page else None
    if next_cursor:
        _start(state, Request(state.query, (*state.stack, next_cursor)))


def request_retry() -> None:
    state = list_state()
    if state.failed:
        _start(state, state.failed)


def request_reload(notice: str | None = None, keep_selected_id: int | None = None) -> None:
    """Load the current page again, keeping filters, sort and page. For use after a change."""
    state = list_state()
    _start(state, Request(state.query, state.stack, keep_selected_id), notice)


def record_gone() -> None:
    """The record was deleted by someone else (PRD-05-R6): explain and update the list."""
    request_reload(notice=RECORD_GONE)


def open_edit(record: dict[str, Any]) -> None:
    """SCREEN-12."""
    from core import record_dialogs  # record_dialogs imports this module.

    record_dialogs.open_edit(record)


def open_delete(record: dict[str, Any]) -> None:
    """SCREEN-13."""
    from core import record_dialogs  # record_dialogs imports this module.

    record_dialogs.open_delete(record)


def _refresh_options(state: ListState) -> None:
    """Filter options and units. A failure here keeps what we have rather than failing the list."""
    try:
        state.categories = api.get("/api/v1/records/categories")["items"]
    except (api.BackendUnavailable, api.ApiError):
        pass
    try:
        items = api.get("/api/v1/category-display")["items"]
        state.display = {item["category"]: item for item in items}
    except (api.BackendUnavailable, api.ApiError):
        pass


def refresh_options() -> None:
    """For a dialog about to show units and ranges: they may have changed since the list loaded."""
    _refresh_options(list_state())


def _commit(state: ListState, request: Request, page: dict[str, Any]) -> None:
    state.query = request.query
    state.stack = request.stack
    state.page = page
    state.pending = None
    state.selected_id = request.keep_selected_id
    state.generation += 1


def run_pending() -> None:
    """Send the pending request. Called with the waiting view already drawn."""
    state = list_state()
    while (request := state.pending) is not None:
        try:
            page = api.get("/api/v1/records", params=request.query.params(request.stack[-1]))
        except api.BackendUnavailable:
            state.pending, state.failed, state.error = None, request, LOAD_ERROR
            return
        except api.ApiError as exc:
            fields = exc.field_errors()
            if exc.status == 422 and "cursor" in fields:
                state.pending, state.notice = replace(request, stack=(None,)), fields["cursor"]
            elif exc.status == 422 and fields:
                state.pending, state.field_errors = None, fields
            else:
                state.pending, state.failed = None, request
                state.error = LOAD_ERROR if exc.status >= 500 else exc.message
            if state.pending is None:
                return
            continue
        if not page["items"] and page["total"] > 0 and len(request.stack) > 1:
            # The rows on this page were deleted: step back a page.
            state.pending = replace(request, stack=request.stack[:-1])
            continue
        _refresh_options(state)
        _commit(state, request, page)


def total_pages(state: ListState) -> int:
    assert state.page is not None
    return max(math.ceil(state.page["total"] / state.page["limit"]), len(state.stack), 1)
