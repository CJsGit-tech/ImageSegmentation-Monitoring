"""SCREEN-10: paged, filtered and sorted records, with a selected row's actions below the table.

The list is one fragment, so querying and paging do not re-run the shell's login check: when the
backend stops answering they show the inline failure and keep the results, while a full run still
goes to SCREEN-04. A run with a pending request draws the waiting view, loads, then reruns.
"""

from dataclasses import replace
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

from core import record_dialogs, records, session
from core.records import ListState, Query

_NEW_RECORD_PAGE = "screens/record_new.py"
_SORT_LABELS = {"timestamp": "時間戳", "title": "標題", "value": "數值", "category": "分類"}
_ORDER_LABELS = {"desc": "遞減（新到舊）", "asc": "遞增（舊到新）"}
_ANOMALY_LABEL = "◆ 異常 🔒"
_NO_RANGE_LABEL = "未設定範圍"
_ANOMALY_ROW_STYLE = "background-color: #FBE3E0"
_ANOMALY_STATUS_STYLE = "background-color: #FBE3E0; color: #A61B12; font-weight: 700"
_SKELETON_ROWS = 8
_SKELETON_ROW_HEIGHT = 32
_CHIP_TEXT_LENGTH = 24
_FIELD_WIDE = 210
_FIELD_NARROW = 150

_TITLE = "records_filter_title"
_CATEGORY = "records_filter_category"
_VALUE_MIN = "records_filter_value_min"
_VALUE_MAX = "records_filter_value_max"
_TIME_FROM = "records_filter_time_from"
_TIME_TO = "records_filter_time_to"
_ANOMALY_ONLY = "records_filter_anomaly_only"
_SORT_BY = "records_sort_by"
_ORDER = "records_order"

_COLUMNS = {
    "狀態": st.column_config.TextColumn("狀態", width=90),
    "標題": st.column_config.TextColumn("標題", width="medium"),
    "分類": st.column_config.TextColumn("分類", width="small"),
    "數值": st.column_config.TextColumn("數值", width="small"),
    "範圍": st.column_config.TextColumn("範圍", width=190, help="數值在此分類目前範圍中的位置"),
    "時間戳": st.column_config.TextColumn("時間戳", width="medium"),
    "創建者": st.column_config.TextColumn("創建者", width="small"),
}


def _applied(state: ListState) -> Query:
    return state.pending.query if state.pending else state.query


def _sync_widgets(query: Query) -> None:
    """Widgets return empty after the page was not drawn (SCREEN-04): fill them from the applied query."""
    defaults = {
        _TITLE: query.title or "",
        _CATEGORY: query.category,
        _VALUE_MIN: query.value_min,
        _VALUE_MAX: query.value_max,
        _TIME_FROM: records.to_widget_time(query.time_from),
        _TIME_TO: records.to_widget_time(query.time_to),
        _ANOMALY_ONLY: query.anomaly_only,
        _SORT_BY: query.sort_by,
        _ORDER: query.order,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _on_submit() -> None:
    state = st.session_state
    records.request_query(
        Query(
            title=(state[_TITLE] or "").strip() or None,
            category=state[_CATEGORY],
            value_min=state[_VALUE_MIN],
            value_max=state[_VALUE_MAX],
            time_from=records.from_widget_time(state[_TIME_FROM]),
            time_to=records.from_widget_time(state[_TIME_TO]),
            anomaly_only=state[_ANOMALY_ONLY],
            sort_by=state[_SORT_BY],
            order=state[_ORDER],
        )
    )


def _remove_filter(*names: str) -> None:
    query = _applied(records.list_state())
    widget_keys = {
        "title": (_TITLE, ""),
        "category": (_CATEGORY, None),
        "value_min": (_VALUE_MIN, None),
        "value_max": (_VALUE_MAX, None),
        "time_from": (_TIME_FROM, None),
        "time_to": (_TIME_TO, None),
        "anomaly_only": (_ANOMALY_ONLY, False),
    }
    changes: dict[str, Any] = {}
    for name in names:
        key, empty = widget_keys[name]
        st.session_state[key] = empty
        changes[name] = False if name == "anomaly_only" else None
    records.request_query(replace(query, **changes))


def _clear_filters() -> None:
    _remove_filter("title", "category", "value_min", "value_max", "time_from", "time_to", "anomaly_only")


def _short(text: str) -> str:
    shortened = text if len(text) <= _CHIP_TEXT_LENGTH else f"{text[:_CHIP_TEXT_LENGTH]}…"
    return records.escape_markdown(shortened)


def _chip_time(value: datetime | None) -> str:
    return value.astimezone(records.display_zone()).strftime("%Y-%m-%d %H:%M") if value else ""


def _chips_for(query: Query) -> list[tuple[str, tuple[str, ...], str]]:
    """(key, filters it removes, label) for each applied filter."""
    chips: list[tuple[str, tuple[str, ...], str]] = []
    if query.title:
        chips.append(("title", ("title",), f"標題：{_short(query.title)}"))
    if query.category:
        chips.append(("category", ("category",), f"分類：{_short(query.category)}"))
    if query.value_min is not None or query.value_max is not None:
        low = "" if query.value_min is None else records.format_number(query.value_min)
        high = "" if query.value_max is None else records.format_number(query.value_max)
        chips.append(("value", ("value_min", "value_max"), f"數值：{low} – {high}"))
    if query.time_from or query.time_to:
        label = f"時間：{_chip_time(query.time_from)} – {_chip_time(query.time_to)}"
        chips.append(("time", ("time_from", "time_to"), label))
    if query.anomaly_only:
        chips.append(("anomaly", ("anomaly_only",), "僅顯示異常"))
    return chips


def _draw_chips(query: Query, *, waiting: bool) -> None:
    chips = _chips_for(query)
    if not chips:
        return
    with st.container(horizontal=True, vertical_alignment="center"):
        for key, names, label in chips:
            st.button(f"{label} ✕", key=f"records_chip_{key}", disabled=waiting, on_click=_remove_filter, args=names)
        st.button("清除全部", key="records_chip_clear", type="tertiary", disabled=waiting, on_click=_clear_filters)


def _category_options(state: ListState) -> list[str]:
    """Values in the data, plus any still chosen: its last record may have just been deleted."""
    options = list(state.categories)
    for chosen in (_applied(state).category, st.session_state.get(_CATEGORY)):
        if chosen and chosen not in options:
            options.append(chosen)
    return options


def _field_error(state: ListState, field: str) -> None:
    message = state.field_errors.get(field)
    if message:
        st.error(message)


def _draw_form(state: ListState) -> None:
    with st.form("records_filter"):
        # Fixed widths in a wrapping row: fields keep a usable size on narrow screens.
        with st.container(horizontal=True):
            with st.container(width=_FIELD_WIDE):
                st.text_input("標題", key=_TITLE, placeholder="（全部）", max_chars=255)
                _field_error(state, "title")
            with st.container(width=_FIELD_WIDE):
                st.selectbox("分類", _category_options(state), index=None, key=_CATEGORY, placeholder="（全部）")
                _field_error(state, "category")
            with st.container(width=_FIELD_NARROW):
                st.number_input("數值最小", value=None, key=_VALUE_MIN, placeholder="最小", format="%g", step=1.0)
                _field_error(state, "value_min")
            with st.container(width=_FIELD_NARROW):
                st.number_input("數值最大", value=None, key=_VALUE_MAX, placeholder="最大", format="%g", step=1.0)
                _field_error(state, "value_max")
            with st.container(width=_FIELD_WIDE):
                st.datetime_input("開始時間", value=None, key=_TIME_FROM, step=60)
                _field_error(state, "time_from")
            with st.container(width=_FIELD_WIDE):
                st.datetime_input("結束時間", value=None, key=_TIME_TO, step=60)
                _field_error(state, "time_to")
            with st.container(width=_FIELD_NARROW):
                st.selectbox("排序欄位", list(_SORT_LABELS), key=_SORT_BY, format_func=_SORT_LABELS.get)
            with st.container(width=_FIELD_WIDE):
                st.selectbox("排序方向", list(_ORDER_LABELS), key=_ORDER, format_func=_ORDER_LABELS.get)
        with st.container(horizontal=True, vertical_alignment="center"):
            st.toggle("僅顯示異常", key=_ANOMALY_ONLY)
            # Stays clickable while waiting: a newer query replaces the older one (M2 section 3).
            st.form_submit_button(
                "查詢中…" if state.pending and state.page else "查詢",
                key="records_query",
                type="primary",
                on_click=_on_submit,
            )


def _frame(state: ListState) -> pd.DataFrame:
    assert state.page is not None
    rows = []
    for record in state.page["items"]:
        display = (state.display or {}).get(record["category"])
        bounds = display["range"] if display else None
        if state.display is None:
            position = ""
        elif bounds:
            position = records.range_position(record["value"], bounds)
        else:
            position = _NO_RANGE_LABEL
        rows.append(
            {
                "狀態": _ANOMALY_LABEL if record["is_anomaly"] else "",
                "標題": record["title"],
                "分類": record["category"],
                "數值": records.format_value(record["value"], display["unit"] if display else None),
                "範圍": position,
                "時間戳": records.format_timestamp(record["timestamp"]),
                "創建者": record["creator_name"],
            }
        )
    return pd.DataFrame(rows, columns=list(_COLUMNS))


def _style(frame: pd.DataFrame, items: list[dict[str, Any]]) -> Any:
    def row_style(row: pd.Series) -> list[str]:
        if not items[row.name]["is_anomaly"]:
            return [""] * len(row)
        return [_ANOMALY_STATUS_STYLE if column == "狀態" else _ANOMALY_ROW_STYLE for column in row.index]

    return frame.style.apply(row_style, axis=1)


def _draw_static_table(state: ListState) -> None:
    assert state.page is not None
    st.dataframe(
        _style(_frame(state), state.page["items"]),
        hide_index=True,
        column_config=_COLUMNS,
        placeholder="",
    )


def _draw_table(state: ListState, *, can_act: bool) -> dict[str, Any] | None:
    """Returns the selected record. Column selection is on so that header clicks cannot sort this page."""
    assert state.page is not None
    items = state.page["items"]
    default = None
    if state.selected_id is not None:
        rows = [index for index, item in enumerate(items) if item["id"] == state.selected_id]
        default = {"selection": {"rows": rows}} if rows else None
    event = st.dataframe(
        _style(_frame(state), items),
        key=f"records_table_{state.generation}",
        hide_index=True,
        column_config=_COLUMNS,
        placeholder="",
        on_select="rerun",
        selection_mode=["single-row", "single-column"] if can_act else "single-column",
        selection_default=default,
        alt="資料記錄表格，異常列的狀態欄標示「異常」",
    )
    rows = event.selection.rows
    return items[rows[0]] if rows and rows[0] < len(items) else None


def _draw_skeleton() -> None:
    with st.container(border=True):
        for _ in range(_SKELETON_ROWS):
            st.skeleton(height=_SKELETON_ROW_HEIGHT)


def _draw_empty(state: ListState, *, can_create: bool) -> None:
    with st.container(border=True, horizontal_alignment="center"):
        if _applied(state).has_filters:
            st.subheader("沒有符合條件的資料", anchor=False, text_alignment="center")
            st.text("試著放寬條件。", text_alignment="center")
            st.button("清除條件", key="records_clear_conditions", on_click=_clear_filters)
        else:
            st.subheader("還沒有任何記錄", anchor=False, text_alignment="center")
            if can_create:
                st.text("新增一筆，或批量導入檔案。", text_alignment="center")
                st.page_link(_NEW_RECORD_PAGE, label="新增資料", width="content")


def _draw_pager(state: ListState, *, waiting: bool) -> None:
    assert state.page is not None
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        st.button(
            "上一頁",
            key="records_previous",
            disabled=waiting or len(state.stack) <= 1,
            on_click=records.request_previous_page,
        )
        st.text(f"第 {len(state.stack)} / {records.total_pages(state)} 頁", width="content")
        st.button(
            "下一頁",
            key="records_next",
            disabled=waiting or not state.page["next_cursor"],
            on_click=records.request_next_page,
        )


def _draw_no_selection_actions() -> None:
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        st.text("選取一列以修改或刪除", width="content")
        st.button("修改", key="records_edit", disabled=True)
        st.button("刪除", key="records_delete", disabled=True)


def _draw_actions(user: session.CurrentUser, record: dict[str, Any] | None) -> None:
    if record is None:
        _draw_no_selection_actions()
        return
    allowed = records.availability(user, record)
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        # st.text, not Markdown: the title and category are user text.
        st.text(f"已選取：{record['title']}（{record['category']} {records.format_number(record['value'])}）", width="content")
        edit = st.button("修改", key="records_edit", disabled=not allowed.enabled)
        delete = st.button(":red[刪除]" if allowed.enabled else "刪除", key="records_delete", disabled=not allowed.enabled)
        if allowed.reason:
            st.text(allowed.reason, width="content")
    if edit:
        records.open_edit(record)
    if delete:
        records.open_delete(record)


def _draw_message(state: ListState) -> None:
    if state.error:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.error(state.error)
            st.button("重試", key="records_retry", on_click=records.request_retry)
    elif state.notice:
        st.info(state.notice)


def _draw_waiting(state: ListState, *, can_act: bool) -> None:
    _draw_chips(_applied(state), waiting=True)
    if state.page is None:
        _draw_skeleton()
        return
    st.info("更新中…顯示的是舊結果。")
    if state.page["items"]:
        _draw_static_table(state)
        _draw_pager(state, waiting=True)
        if can_act:
            _draw_no_selection_actions()


def _draw_result(state: ListState, user: session.CurrentUser) -> None:
    can_act = records.can_act_on_records(user)
    _draw_chips(state.query, waiting=False)
    _draw_message(state)
    if state.page is None:
        return
    if not state.page["items"]:
        _draw_empty(state, can_create=session.can("records.create"))
        return
    selected = _draw_table(state, can_act=can_act)
    st.caption(f"時間以 {records.display_zone().key} 顯示")
    _draw_pager(state, waiting=False)
    if can_act:
        _draw_actions(user, selected)


def _rerun_list() -> None:
    """Redraw from the loaded state. A fragment rerun stays one, so it skips the shell's login check."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:  # Raised during a full run, where only a full rerun is allowed.
        st.rerun()


@st.fragment
def _records_list() -> None:
    state = records.list_state()
    user = session.current_user()
    assert user is not None
    _sync_widgets(_applied(state))
    _draw_form(state)
    if state.pending:
        _draw_waiting(state, can_act=records.can_act_on_records(user))
        records.run_pending()
        _rerun_list()
    _draw_result(state, user)


with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    st.title("資料列表", anchor=False, width="content")
    if session.can("records.create"):
        st.page_link(_NEW_RECORD_PAGE, label="新增資料", width="content")

_records_list()
record_dialogs.draw_open_dialog()
