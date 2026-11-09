"""SCREEN-44: each category's anomaly range and unit, set from a dialog on the selected row.

The dialog is reopened from state on every full run, because a failed save reruns the whole app
(FormSubmit), and with the backend down that run draws SCREEN-04 and the dialog returns afterwards.
The list loads only when asked to, so that a selected row index always refers to the rows drawn.
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st

from core import api, forms, records, ui

_RULE_TEXT = (
    "數值小於或等於最小值，或大於或等於最大值，會被標為異常（含邊界）。"
    "修改只影響之後的新資料，已保存的資料不會重新判斷。單位只用於顯示。"
)
_UNIT_NOTE = "修改單位後，所有畫面（含歷史資料）會改以新單位顯示；已保存資料與異常標記不變。"
_NO_DATA_NOTE = "「尚無資料」表示目前還沒有此分類的記錄；設定的範圍會套用到之後的資料。"
_CLEAR_HINT = "要清除範圍，請關閉此視窗並使用「清除範圍」。"
_LOAD_ERROR = "無法載入分類，請再試一次。"
_CLEAR_FAILED = "無法清除範圍，請再試一次。"
_CLEAR_TIMEOUT = "系統回應逾時，請確認此分類的範圍後再試一次。"
_NO_RANGE_REASON = "尚未設定範圍，沒有可清除的內容"
_NO_SELECTION = "選取一列以設定範圍"
_NO_DATA = "尚無資料"
_SET = "已設定"
_UNSET = "未設定"
_EMPTY_CELL = "—"
_BAND = "┄├─────┤┄"
_UNSET_STYLE = "background-color: #FFF3DC; color: #9A5B00; font-weight: 700"
_UNIT_MAX_LENGTH = 32
_SKELETON_ROWS = 6
_SKELETON_ROW_HEIGHT = 32

# Kept under the prefix forms.reset_interrupted_submits() clears, because that is the shell's only
# signal that the page changed, and this has to survive runs where the page does not draw (SCREEN-04).
_STATE_KEY = "__submit__anomaly_ranges_page"
_MIN = "anomaly_ranges_min"
_MAX = "anomaly_ranges_max"
_UNIT = "anomaly_ranges_unit"

_COLUMNS = {
    "狀態": st.column_config.TextColumn("狀態", width=90),
    "分類": st.column_config.TextColumn("分類", width="medium"),
    "最小值": st.column_config.TextColumn("最小值", width="small"),
    "最大值": st.column_config.TextColumn("最大值", width="small"),
    "單位": st.column_config.TextColumn("單位", width="small"),
    "範圍帶": st.column_config.TextColumn("範圍帶", width=170),
    "資料": st.column_config.TextColumn("資料", width="small"),
}


@dataclass
class PageState:
    items: list[dict[str, Any]] | None = None
    unset_count: int = 0
    needs_load: bool = True
    load_error: str | None = None
    notice: str | None = None
    action_error: str | None = None
    selected: str | None = None
    editing: dict[str, Any] | None = None
    generation: int = 0
    opened: int = 0


def _state() -> PageState:
    return st.session_state.setdefault(_STATE_KEY, PageState())


def _bounds(bounds: dict[str, float]) -> tuple[str, str]:
    return records.format_number(float(bounds["min"])), records.format_number(float(bounds["max"]))


def _setting_text(item: dict[str, Any]) -> str:
    """Plain text: pass it through records.escape_markdown before any Markdown element."""
    if not item["range"]:
        return f"未設定範圍（單位：{item['unit']}）" if item["unit"] else "未設定範圍"
    low, high = _bounds(item["range"])
    return f"{low} – {high} {item['unit']}" if item["unit"] else f"{low} – {high}"


def _load(state: PageState) -> None:
    state.needs_load = False
    try:
        body = api.get("/api/v1/category-settings")
    except api.BackendUnavailable:
        state.load_error = _LOAD_ERROR
        return
    except api.ApiError as exc:
        state.load_error = _LOAD_ERROR if exc.status >= 500 else exc.message
        return
    state.items = body["items"]
    state.unset_count = body["unset_count"]
    state.load_error = None
    state.generation += 1


def _request_load() -> None:
    _state().needs_load = True


# --- Set range dialog ---


def _open_editor(item: dict[str, Any]) -> None:
    state = _state()
    state.editing = item
    state.opened += 1
    state.notice = None
    state.action_error = None
    bounds = item["range"] or {}
    st.session_state[_MIN] = bounds.get("min")
    st.session_state[_MAX] = bounds.get("max")
    st.session_state[_UNIT] = item["unit"] or ""


def _close_editor() -> None:
    """An interrupted or timed-out save may still have taken effect, so read the list again."""
    state = _state()
    state.editing = None
    state.needs_load = True


def _changes(item: dict[str, Any], low: float | None, high: float | None, unit_text: str | None) -> dict[str, Any]:
    """Only what changed, so that a save cannot undo another Admin's change to the other value.

    Clearing has its own confirmed action: blank bounds go to the backend, which refuses them.
    """
    unit = (unit_text or "").strip() or None
    changes: dict[str, Any] = {}
    if unit != item["unit"]:
        changes["unit"] = unit
    blank = low is None and high is None
    if blank and not item["range"]:
        return changes or {"range": {"min": None, "max": None}}
    if {"min": low, "max": high} != item["range"]:
        changes["range"] = {"min": low, "max": high}
    return changes


def _saved_notice(item: dict[str, Any], changes: dict[str, Any], saved: dict[str, Any]) -> str:
    name = records.escape_markdown(saved["category"])
    if "range" in changes:
        return f"已儲存「{name}」的範圍：{records.escape_markdown(_setting_text(saved))}（之後的新資料依此判斷）。"
    if saved["unit"] is None:
        return f"已清除「{name}」的單位。"
    unit = records.escape_markdown(saved["unit"])
    if saved["range"]:
        return f"已儲存「{name}」的單位：{unit}（所有畫面含歷史資料改以新單位顯示）。"
    return f"已儲存「{name}」的單位：{unit}。此分類仍未設定範圍，其資料不會被判斷為異常。"


def _error_slot(message: str | None) -> None:
    """Always takes a place in the layout. An element that appears only after an error shifts the
    ones below it, and during the next send the old buttons stay on screen, still clickable."""
    slot = st.empty()
    if message:
        slot.error(message)


def _range_error(submit: forms.FormSubmit, *, under_min: bool) -> str | None:
    """The backend reports both range errors on the range as a whole, so place them here."""
    low, high = submit.values.get(_MIN), submit.values.get(_MAX)
    if low is not None and high is not None:
        show = under_min  # Reversed: the visual puts this under the minimum.
    else:
        show = (low if under_min else high) is None
    return submit.field_error("range") if show else None


def _draw_editor_body(item: dict[str, Any]) -> None:
    state = _state()
    if state.editing is None:
        st.rerun()

    submit = forms.FormSubmit(f"anomaly_ranges_save_{state.opened}", fields=[_MIN, _MAX, _UNIT])
    st.text(f"目前：{_setting_text(item)}")
    with st.empty():
        # A field error is shown at its field; the form-level text then only repeats it generically.
        if not (submit.field_error("range") or submit.field_error("unit")):
            submit.show_form_error()
    with st.form("anomaly_ranges_form", border=False, enter_to_submit=False):
        # %g shows the whole value: the default format would round it to two decimals.
        st.number_input("最小值", value=None, key=_MIN, format="%g", step=1.0, disabled=submit.busy)
        _error_slot(_range_error(submit, under_min=True))
        st.number_input("最大值", value=None, key=_MAX, format="%g", step=1.0, disabled=submit.busy)
        _error_slot(_range_error(submit, under_min=False))
        st.text_input("單位（選填）", key=_UNIT, max_chars=_UNIT_MAX_LENGTH, disabled=submit.busy)
        _error_slot(submit.field_error("unit"))
        st.caption(_UNIT_NOTE)
        if item["range"]:
            st.caption(_CLEAR_HINT)
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.form_submit_button("取消", key="anomaly_ranges_cancel", disabled=submit.busy, on_click=_close_editor)
            st.form_submit_button(
                submit.label("儲存"),
                key="anomaly_ranges_save",
                type="primary",
                disabled=submit.busy,
                on_click=submit.begin,
            )

    def save() -> tuple[dict[str, Any], dict[str, Any]]:
        changes = _changes(item, submit.values[_MIN], submit.values[_MAX], submit.values[_UNIT])
        if not changes:
            return changes, item
        return changes, api.patch("/api/v1/category-settings", json={"category": item["category"], **changes})

    def saved(result: tuple[dict[str, Any], dict[str, Any]]) -> None:
        changes, row = result
        state.notice = _saved_notice(item, changes, row) if changes else "沒有變更。"
        state.selected = item["category"]
        state.editing = None
        state.needs_load = bool(changes)
        st.rerun()

    submit.run(save, on_success=saved)


def _draw_editor(item: dict[str, Any]) -> None:
    @st.dialog(f"設定範圍：{records.escape_markdown(item['category'])}", on_dismiss=_close_editor)
    def _dialog() -> None:
        _draw_editor_body(item)

    _dialog()


# --- Clearing ---


def _clear_range(item: dict[str, Any]) -> None:
    state = _state()
    state.notice = None
    state.action_error = None
    state.needs_load = True
    try:
        with st.spinner("處理中…"):
            api.patch("/api/v1/category-settings", json={"category": item["category"], "range": None})
    except api.BackendTimeout:
        state.action_error = _CLEAR_TIMEOUT
    except api.BackendUnavailable:
        state.action_error = _CLEAR_FAILED
    except api.ApiError as exc:
        state.action_error = exc.message
    else:
        name = records.escape_markdown(item["category"])
        state.notice = f"已清除「{name}」的範圍。之後的新資料不會判斷異常，已標為異常的記錄不變。"


def _confirm_clear(item: dict[str, Any]) -> None:
    name = records.escape_markdown(item["category"])
    low, high = _bounds(item["range"])
    unit = f"單位 {records.escape_markdown(item['unit'])} 會保留。" if item["unit"] else ""
    ui.confirm_dialog(
        f"清除範圍：{name}",
        target=f"將清除「{name}」目前的範圍：{records.escape_markdown(f'{low} – {high}')}。{unit}",
        consequence="清除後，這個分類之後的新資料不會再檢查是否異常。已標為異常的記錄維持異常。之後可以再次設定範圍。",
        confirm_label="清除範圍",
        on_confirm=lambda: _clear_range(item),
    )


# --- The list ---


def _frame(items: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in items:
        low, high = _bounds(item["range"]) if item["range"] else (_EMPTY_CELL, _EMPTY_CELL)
        rows.append(
            {
                "狀態": _SET if item["range"] else _UNSET,
                "分類": item["category"],
                "最小值": low,
                "最大值": high,
                "單位": item["unit"] or _EMPTY_CELL,
                "範圍帶": _BAND if item["range"] else "",
                "資料": "" if item["has_data"] else _NO_DATA,
            }
        )
    return pd.DataFrame(rows, columns=list(_COLUMNS))


def _style(frame: pd.DataFrame) -> Any:
    def status_style(row: pd.Series) -> list[str]:
        return [_UNSET_STYLE if column == "狀態" and row[column] == _UNSET else "" for column in row.index]

    return frame.style.apply(status_style, axis=1)


def _draw_table(state: PageState) -> dict[str, Any] | None:
    """Returns the selected item. Column selection is on so that header clicks cannot re-sort."""
    items = state.items or []
    rows = [index for index, item in enumerate(items) if item["category"] == state.selected]
    event = st.dataframe(
        _style(_frame(items)),
        key=f"anomaly_ranges_table_{state.generation}",
        hide_index=True,
        column_config=_COLUMNS,
        placeholder="",
        on_select="rerun",
        selection_mode=["single-row", "single-column"],
        selection_default={"selection": {"rows": rows}} if rows else None,
        alt="分類的異常範圍與單位表格，未設定範圍的分類排在最前，狀態欄標示「未設定」",
    )
    picked = event.selection.rows
    selected = items[picked[0]] if picked and picked[0] < len(items) else None
    state.selected = selected["category"] if selected else None
    return selected


def _draw_actions(selected: dict[str, Any] | None) -> None:
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        if selected is None:
            st.text(_NO_SELECTION, width="content")
            st.button("設定範圍", key="anomaly_ranges_edit", disabled=True)
            st.button("清除範圍", key="anomaly_ranges_clear", disabled=True)
            return
        no_data = f"（{_NO_DATA}）" if not selected["has_data"] else ""
        # st.text, not Markdown: the category and unit are user text.
        st.text(f"已選取：{selected['category']}（{_setting_text(selected)}）{no_data}", width="content")
        st.button("設定範圍", key="anomaly_ranges_edit", on_click=_open_editor, args=(selected,))
        if selected["range"]:
            if st.button("清除範圍", key="anomaly_ranges_clear"):
                _confirm_clear(selected)
        else:
            ui.disabled_action("清除範圍", _NO_RANGE_REASON, key="anomaly_ranges_clear")


def _draw_messages(state: PageState) -> None:
    if state.notice:
        st.success(state.notice)
    if state.action_error:
        st.error(state.action_error)
    if state.load_error:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.error(state.load_error)
            st.button("重試", key="anomaly_ranges_retry", on_click=_request_load)
    if state.items and state.unset_count:
        st.warning(f"**{state.unset_count} 個分類尚未設定範圍**：其資料不會被判斷為異常。")


def _draw_skeleton() -> None:
    with st.container(border=True):
        for _ in range(_SKELETON_ROWS):
            st.skeleton(height=_SKELETON_ROW_HEIGHT)


def _draw_empty() -> None:
    with st.container(border=True, horizontal_alignment="center"):
        st.subheader("還沒有任何分類", anchor=False, text_alignment="center")
        st.text("資料中出現分類後會列在這裡。", text_alignment="center")


def _page() -> None:
    state = _state()
    st.title("異常範圍設定", anchor=False)
    if state.needs_load:
        if state.items is None:
            holder = st.empty()
            with holder.container():
                _draw_skeleton()
            _load(state)
            holder.empty()
        else:
            _load(state)
    _draw_messages(state)
    st.caption(_RULE_TEXT)
    if state.items is None:
        return
    if not state.items:
        _draw_empty()
        return
    selected = _draw_table(state)
    if any(not item["has_data"] for item in state.items):
        st.caption(_NO_DATA_NOTE)
    _draw_actions(selected)
    if state.editing is not None:
        _draw_editor(state.editing)


_page()
