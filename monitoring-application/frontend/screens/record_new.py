"""SCREEN-11: create one record, warned before saving when it would become anomalous.

Plain widgets rather than st.form, so that the warning and the disabled submit can appear before
submitting (see core/record_form.py). Enter therefore commits a field and does not submit.
Every slot that may hold a message always exists, so that the buttons never change position:
an element that appears only after an error leaves stale, clickable buttons during the next send.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import streamlit as st

from core import api, forms, record_form, records

_PREFIX = "record_new"
_RECORDS_PAGE = "screens/records.py"
_FORM_WIDTH = 640
_VALUE_PAUSE = "400ms"
_TITLE_MAX_LENGTH = 255
_CATEGORY_MAX_LENGTH = 100
_DISPLAY_TIMEOUT_SECONDS = 5

_TITLE_REQUIRED = "請輸入標題"
_CATEGORY_REQUIRED = "請選擇或輸入分類"
_CATEGORY_TOO_LONG = f"分類不可超過 {_CATEGORY_MAX_LENGTH} 個字元"
_FIX_FIELDS = "請修正標示的欄位。"
_CATEGORY_PLACEHOLDER = "選擇既有分類，或輸入新分類"
_CATEGORY_HINT = "輸入新分類後請按 Enter 或選取「Add」。"
_TIME_HINT = "預設為現在，可修改"
_ERROR_FIELDS = ("title", "value", "category", "timestamp")

# Kept under the prefix forms.reset_interrupted_submits() clears: changing page starts over.
_STATE_KEY = "__submit__record_new_page"


@dataclass
class PageState:
    generation: int = 0
    categories: list[str] | None = None
    default_time: datetime | None = None
    mirror: dict[str, Any] = field(default_factory=dict)
    saved: dict[str, Any] | None = None
    saved_note: str | None = None
    saved_unit: str | None = None
    # One key per distinct content: a retry after a timeout cannot add a second record.
    request_content: tuple[Any, ...] | None = None
    request_id: str | None = None


def _state() -> PageState:
    return st.session_state.setdefault(_STATE_KEY, PageState())


def _key(state: PageState, name: str) -> str:
    return f"record_new_{name}_{state.generation}"


def _now_for_widget() -> datetime:
    now = records.to_widget_time(datetime.now(UTC))
    assert now is not None
    return now.replace(second=0, microsecond=0)


def _start_new_form() -> None:
    state = _state()
    state.saved, state.saved_note, state.saved_unit = None, None, None
    state.categories, state.default_time = None, None
    state.mirror = {}
    state.request_content, state.request_id = None, None
    state.generation += 1
    record_form.clear(_PREFIX)


def _load_categories(state: PageState) -> list[str]:
    """Typing a category still works without the list, so a failure here only retries later."""
    if state.categories is None:
        try:
            state.categories = api.get("/api/v1/records/categories")["items"]
        except (api.BackendUnavailable, api.ApiError):
            return []
    return state.categories


def _display_item(category: str) -> dict[str, Any] | None:
    try:
        items = api.get("/api/v1/category-display", timeout=_DISPLAY_TIMEOUT_SECONDS)["items"]
    except (api.BackendUnavailable, api.ApiError):
        return None
    return next((item for item in items if item["category"] == category), None)


def _restore_after_outage(state: PageState) -> None:
    """SCREEN-04 replaces this page while the backend is down, which drops the widgets' values."""
    for key, value in state.mirror.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _request_id(state: PageState, body: dict[str, Any]) -> str:
    """The same key for a resend of the same content. Changed content is a new record."""
    content = (body["title"], body["value"], body["category"], body.get("timestamp"))
    if state.request_id is None or state.request_content != content:
        state.request_content, state.request_id = content, str(uuid4())
    return state.request_id


def _send(state: PageState, values: dict[str, Any]) -> dict[str, Any]:
    title = (values[_key(state, "title")] or "").strip()
    value_text = values[_key(state, "value")]
    category = (values[_key(state, "category")] or "").strip()

    problems = []
    if not title:
        problems.append({"field": "title", "message": _TITLE_REQUIRED})
    if message := record_form.value_error(value_text):
        problems.append({"field": "value", "message": message})
    if not category:
        problems.append({"field": "category", "message": _CATEGORY_REQUIRED})
    elif len(category) > _CATEGORY_MAX_LENGTH:
        # A selectbox has no max_chars, and the backend words this one in English.
        problems.append({"field": "category", "message": _CATEGORY_TOO_LONG})
    if problems:
        raise api.ApiError(422, "VALIDATION_ERROR", _FIX_FIELDS, problems)

    value = record_form.parse_value(value_text)
    assert value is not None
    chosen_time = values[_key(state, "time")]

    def post(confirm: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title, "value": value, "category": category, "confirm_anomaly": confirm}
        # An untouched field means "now" at the moment of saving (PRD-04-R8), not when the page opened.
        if chosen_time is not None and chosen_time != state.default_time:
            zoned = records.from_widget_time(chosen_time)
            assert zoned is not None
            body["timestamp"] = zoned.isoformat()
        body["client_request_id"] = _request_id(state, body)
        return api.post("/api/v1/records", json=body)

    return record_form.send_with_confirmation(_PREFIX, category, value, values[record_form.confirm_key(_PREFIX)], post)


def _field_error(slot: Any, message: str | None) -> None:
    if message:
        slot.error(message)


def _required_error(submit: forms.FormSubmit, name: str, required_message: str, text: str | None) -> str | None:
    """A required-field error goes away once the field is filled. Backend errors wait for the next send."""
    message = submit.field_error(name)
    return None if message == required_message and (text or "").strip() else message


def _value_field_error(submit: forms.FormSubmit, text: str | None) -> str | None:
    message = submit.field_error("value")
    if message in (record_form.VALUE_REQUIRED, record_form.VALUE_NOT_NUMBER):
        return record_form.value_error(text)
    return message


def _category_field_error(submit: forms.FormSubmit, text: str | None) -> str | None:
    message = _required_error(submit, "category", _CATEGORY_REQUIRED, text)
    return None if message == _CATEGORY_TOO_LONG and len((text or "").strip()) <= _CATEGORY_MAX_LENGTH else message


def _draw_form(state: PageState) -> None:
    title_key, value_key = _key(state, "title"), _key(state, "value")
    category_key, time_key = _key(state, "category"), _key(state, "time")
    widget_keys = [title_key, value_key, category_key, time_key]
    categories = _load_categories(state)

    _restore_after_outage(state)
    submit = forms.FormSubmit(_PREFIX, fields=[*widget_keys, record_form.confirm_key(_PREFIX)])
    if state.default_time is None:
        state.default_time = _now_for_widget()
    if time_key not in st.session_state:
        st.session_state[time_key] = state.default_time

    form_error = st.empty()
    with st.container(border=True, width=_FORM_WIDTH):
        title = st.text_input("標題", key=title_key, max_chars=_TITLE_MAX_LENGTH, disabled=submit.busy)
        title_error = st.empty()
        value_text = st.text_input("數值", key=value_key, live=_VALUE_PAUSE, disabled=submit.busy)
        value_error = st.empty()
        range_hint = st.empty()
        category = st.selectbox(
            "分類",
            categories,
            index=None,
            key=category_key,
            placeholder=_CATEGORY_PLACEHOLDER,
            accept_new_options=True,
            disabled=submit.busy,
        )
        category_error = st.empty()
        st.caption(_CATEGORY_HINT)
        st.datetime_input("時間戳", value=None, key=time_key, step=60, format="YYYY-MM-DD", disabled=submit.busy)
        time_error = st.empty()
        st.caption(_TIME_HINT)

        gate = record_form.draw_anomaly_gate(_PREFIX, category, record_form.parse_value(value_text), busy=submit.busy)

        with st.container(horizontal=True):
            st.button(
                submit.label("送出"),
                key="record_new_submit",
                type="primary",
                disabled=submit.busy or gate.blocks_submit,
                on_click=submit.begin,
            )
            cancelled = st.button("取消", key="record_new_cancel", disabled=submit.busy)

    if cancelled:
        st.switch_page(_RECORDS_PAGE)

    errors = {
        "title": _required_error(submit, "title", _TITLE_REQUIRED, title),
        "value": _value_field_error(submit, value_text),
        "category": _category_field_error(submit, category),
        "timestamp": submit.field_error("timestamp"),
    }
    _field_error(title_error, errors["title"])
    _field_error(value_error, errors["value"])
    _field_error(category_error, errors["category"])
    _field_error(time_error, errors["timestamp"])

    hint = gate.range_text
    name = (category or "").strip()
    if not gate.checked and name and not submit.busy:
        item = _display_item(name)
        hint = record_form.range_text(item["range"], item["unit"]) if item else None
    if hint:
        range_hint.caption(records.escape_markdown(hint))

    has_field_error = any(submit.field_error(name) for name in _ERROR_FIELDS)
    if not gate.conflict and not has_field_error:
        with form_error:
            submit.show_form_error()

    if not submit.busy:
        state.mirror = {key: st.session_state.get(key) for key in widget_keys}

    def saved(record: dict[str, Any]) -> None:
        state.saved = record
        state.saved_note = record_form.saved_note(_PREFIX, record)
        item = _display_item(record["category"])
        state.saved_unit = item["unit"] if item else None
        st.rerun()

    submit.run(lambda: _send(state, submit.values), on_success=saved)


def _draw_saved(state: PageState) -> None:
    record = state.saved
    assert record is not None
    # Everything shown comes from the response: this is what was actually stored.
    message = f"已新增 1 筆記錄。{state.saved_note or ''}"
    st.success(message, width=_FORM_WIDTH)
    with st.container(border=True, width=_FORM_WIDTH):
        # st.text, not Markdown: the title and category are user text.
        st.text(f"標題：{record['title']}")
        st.text(f"數值：{records.format_value(float(record['value']), state.saved_unit)}")
        st.text(f"分類：{record['category']}")
        st.text(f"時間戳：{records.format_timestamp(record['timestamp'])}")
        with st.container(horizontal=True, vertical_alignment="center"):
            st.button("繼續新增", key="record_new_continue", type="primary", on_click=_start_new_form)
            st.page_link(_RECORDS_PAGE, label="前往資料列表", width="content")


def _page() -> None:
    state = _state()
    st.title("新增資料", anchor=False)
    if state.saved is not None:
        _draw_saved(state)
    else:
        _draw_form(state)


_page()
