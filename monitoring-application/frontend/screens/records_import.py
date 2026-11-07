"""SCREEN-14: upload a CSV or JSON file; the whole file is written, or nothing is (PRD-06).

The result has one slot below the card that always exists, so nothing is ever inserted above
the buttons: an element that appears only after an error leaves stale, clickable buttons
during the next send (see screens/admin_anomaly_ranges.py).

The picker has no `type=` filter on purpose: the backend refuses other formats with its own
Chinese explanation (415), which Streamlit's client-side filter would replace with English.
"""

from dataclasses import dataclass
from typing import Any

import streamlit as st

from core import api, forms, records

_PATH = "/api/v1/records/imports"
_RECORDS_PAGE = "screens/records.py"
_UPLOADER = "records_import_file"
_CARD_WIDTH = 720
_ERRORS_PER_PAGE = 25
# Kept under the prefix forms.reset_interrupted_submits() clears: changing page starts over.
_STATE_KEY = "__submit__records_import_page"

_FORMAT_NOTE = (
    "欄位：`title`（標題，必填）、`value`（數值，必填）、`category`（分類，必填）、`timestamp`（時間戳，可省略）。"
    "時間戳為含時區位移的 ISO-8601，例如 `2026-09-01T00:14:42+08:00`；省略時為導入當下。"
    "單位與創建者不從檔案讀取，創建者為目前使用者。\n\n"
    "CSV：UTF-8，第一列為標題列 `title,value,category,timestamp`。JSON：最外層為物件陣列，每個物件使用上列的鍵。"
    "上限 5 MB、10,000 筆。"
)
_NO_FILE_REASON = "請先選擇 .csv 或 .json 檔案"
_PROCESSING = "處理中…（無法量測進度）。導覽仍可使用。"
_ROWS_REFUSED = "有問題，這個檔案的資料一筆都沒有寫入。請修正後可用原檔名重新上傳。"
_ROW_NUMBER_NOTE = "CSV 的列號含標題列（標題列為第 1 列，與 Excel 相同）；JSON 為陣列中的第幾筆，從 1 起算。"
_NAME_TAKEN = "相同檔名已成功導入過，請改檔名後再上傳。"
_SAME_CONTENT = (
    "這個檔案的內容與先前導入的完全相同：資料已經寫入，不需要再上傳（例如上次上傳逾時但實際已完成）。"
    "可到資料列表確認。"
)
_TIMEOUT = (
    "系統回應逾時，無法確定是否已導入。請用同一個檔案再按一次「開始導入」："
    "若已導入，會顯示相同內容已成功導入過。"
)
_FILE_MISSING = "沒有收到檔案，請重新選擇檔案後再試一次。"
_FIELD_LABELS = {"title": "標題", "value": "數值", "category": "分類", "timestamp": "時間戳"}


@dataclass
class PageState:
    # (name, bytes, MIME type) captured when 開始導入 is pressed: the picker is disabled while sending.
    pending: tuple[str, bytes, str] | None = None
    last_name: str | None = None
    outcome: dict[str, Any] | None = None
    error_page: int = 0


def _state() -> PageState:
    return st.session_state.setdefault(_STATE_KEY, PageState())


def _file_changed() -> None:
    """A new (or corrected) file starts clean, including one with the same name (FLOW-11)."""
    state = _state()
    state.outcome, state.last_name, state.error_page = None, None, 0


def _begin(submit: forms.FormSubmit) -> None:
    uploaded = st.session_state.get(_UPLOADER)
    if uploaded is None:
        return
    state = _state()
    state.pending = (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")
    state.last_name = uploaded.name
    state.outcome, state.error_page = None, 0
    submit.begin()


def _send(state: PageState) -> dict[str, Any]:
    """Import refusals and a timeout become outcomes; anything else is FormSubmit's form error."""
    assert state.pending is not None
    name, data, mime = state.pending
    try:
        body = api.post(_PATH, files={"file": (name, data, mime)})
    except api.BackendTimeout:
        return {"kind": "timeout"}
    except api.ApiError as exc:
        if not exc.code.startswith("IMPORT_"):
            raise
        details = exc.details if isinstance(exc.details, dict) else {}
        return {"kind": "refused", "code": exc.code, "message": exc.message, "details": details}
    return {"kind": "success", "body": body}


def _error(text: str) -> None:
    st.error(records.escape_markdown(text))


def _problem_text(problem: dict[str, Any]) -> str:
    field, message = problem.get("field"), str(problem.get("message", ""))
    if not field:
        return message
    return f"{_FIELD_LABELS.get(field, field)}：{message}"


def _previous_page() -> None:
    _state().error_page -= 1


def _next_page() -> None:
    _state().error_page += 1


def _draw_rows_refused(state: PageState, details: dict[str, Any]) -> None:
    problems = details.get("errors") or []
    count = details.get("error_count", len(problems))
    _error(_ROWS_REFUSED)
    summary = f"共 {count} 個問題。"
    if details.get("truncated"):
        summary += f"只列出前 {len(problems)} 個，修正後再上傳會顯示其餘的問題。"
    st.text(summary)
    pages = max(1, -(-len(problems) // _ERRORS_PER_PAGE))
    state.error_page = min(max(state.error_page, 0), pages - 1)
    start = state.error_page * _ERRORS_PER_PAGE
    shown = problems[start : start + _ERRORS_PER_PAGE]
    # st.dataframe cells are plain text: column names and values from the file are not parsed.
    st.dataframe(
        {"列號": [p.get("row") for p in shown], "問題": [_problem_text(p) for p in shown]},
        hide_index=True,
        column_config={
            "列號": st.column_config.NumberColumn("列號", width="small", format="%d"),
            "問題": st.column_config.TextColumn("問題", width="large"),
        },
    )
    st.caption(_ROW_NUMBER_NOTE)
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        st.button("上一頁", key="records_import_prev", disabled=state.error_page == 0, on_click=_previous_page)
        st.text(f"第 {state.error_page + 1} / {pages} 頁")
        st.button("下一頁", key="records_import_next", disabled=state.error_page >= pages - 1, on_click=_next_page)


def _draw_name_taken(details: dict[str, Any]) -> None:
    _error(_NAME_TAKEN)
    previous = details.get("previous_import") or {}
    if previous.get("imported_at"):
        st.text(f"先前導入：{records.format_timestamp(previous['imported_at'])}，{previous.get('row_count')} 筆。")
    if previous.get("same_content"):
        st.info(_SAME_CONTENT)


def _unparseable_text(message: str, details: dict[str, Any]) -> str:
    reason = details.get("reason")
    where = ""
    if reason == "invalid_json" and details.get("line") is not None:
        where = f"（第 {details['line']} 行第 {details.get('column')} 欄）"
    elif reason == "csv_malformed" and details.get("line") is not None:
        where = f"（第 {details['line']} 行）"
    return f"檔案無法解析，整個檔案已被拒絕：{message}{where}。"


def _draw_refused(state: PageState, outcome: dict[str, Any]) -> None:
    code, message, details = outcome["code"], outcome["message"], outcome["details"]
    if code == "IMPORT_ROWS_INVALID":
        _draw_rows_refused(state, details)
    elif code == "IMPORT_FILENAME_TAKEN":
        _draw_name_taken(details)
    elif code == "IMPORT_UNSUPPORTED_FORMAT":
        _error(f"{message}，整個檔案已被拒絕。")
    elif code == "IMPORT_FILE_UNPARSEABLE":
        _error(_unparseable_text(message, details))
        if details.get("columns"):
            st.text(f"欄位：{'、'.join(str(name) for name in details['columns'])}")
    elif code == "IMPORT_FILE_TOO_LARGE":
        limit = details.get("max_bytes")
        _error(f"{message}（上限 {limit // (1024 * 1024)} MB）。" if limit else f"{message}。")
    elif code == "IMPORT_TOO_MANY_ROWS":
        extra = ""
        if details.get("row_count") is not None and details.get("max_rows") is not None:
            extra = f"（共 {details['row_count']:,} 筆，上限 {details['max_rows']:,} 筆）"
        _error(f"{message}{extra}。")
    elif code == "IMPORT_FILE_MISSING":
        _error(_FILE_MISSING)
    else:
        # Most backend messages already say 未導入 (empty file, invalid name); do not say it twice.
        _error(f"{message}。" if "未導入" in message else f"{message}，整個檔案未導入。")


def _draw_outcome(state: PageState, outcome: dict[str, Any]) -> None:
    if outcome["kind"] == "success":
        body = outcome["body"]
        with st.container(horizontal=True, vertical_alignment="center"):
            st.success(f"已導入 **{int(body['imported_count'])}** 筆。")
            st.page_link(_RECORDS_PAGE, label="前往資料列表", width="content")
        st.text(f"檔案：{body['filename']}")
    elif outcome["kind"] == "timeout":
        st.warning(_TIMEOUT)
    else:
        _draw_refused(state, outcome)


def _page() -> None:
    state = _state()
    st.title("批量導入", anchor=False)
    submit = forms.FormSubmit("records_import", fields=[])

    with st.container(border=True, width=_CARD_WIDTH):
        uploaded = st.file_uploader(
            "選擇檔案（.csv 或 .json）",
            key=_UPLOADER,
            on_change=_file_changed,
            disabled=submit.busy,
        )
        selected_slot = st.empty()
        st.caption(_FORMAT_NOTE)
        with st.container(horizontal=True, vertical_alignment="center"):
            st.button(
                submit.label("開始導入"),
                key="records_import_submit",
                type="primary",
                disabled=submit.busy or uploaded is None,
                on_click=_begin,
                args=(submit,),
            )
            reason_slot = st.empty()

    # The picker may come back empty after being disabled; the name it held stays visible.
    if uploaded is None and state.last_name and (submit.busy or state.outcome is not None):
        selected_slot.text(f"已選檔案：{state.last_name}")
    if uploaded is None and not submit.busy:
        reason_slot.caption(_NO_FILE_REASON)

    result_slot = st.empty()
    if submit.busy:
        result_slot.info(_PROCESSING)
    else:
        with result_slot.container():
            submit.show_form_error()
            if state.outcome is not None:
                _draw_outcome(state, state.outcome)

    def finished(outcome: dict[str, Any]) -> None:
        state.pending = None
        state.outcome = outcome
        st.rerun()

    submit.run(lambda: _send(state), on_success=finished)


_page()
