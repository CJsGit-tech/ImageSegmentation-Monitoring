"""SCREEN-12 and SCREEN-13: edit a record or permanently delete it, in a dialog over SCREEN-10.

Opening checks the record, stores it here and reruns the app. draw_open_dialog() then draws the
dialog on every full run for as long as the state exists, because a failed save reruns the whole
app (FormSubmit), and with the backend down that run draws SCREEN-04 and the dialog returns after.

Every slot that may hold a message always exists: an element that appears only after an error
shifts the ones below it, and during the next send the old buttons stay on screen, still clickable.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import streamlit as st
from streamlit.errors import StreamlitAPIException

from core import api, forms, record_form, records, session

_EDIT_PREFIX = "record_edit"
_EDIT = "edit"
_DELETE = "delete"
_DIALOG_WIDTH = "medium"
_VALUE_PAUSE = "400ms"
_TITLE_MAX_LENGTH = 255
_CATEGORY_MAX_LENGTH = 100
_OPEN_TIMEOUT_SECONDS = 10

_EDIT_TITLE = "修改記錄"
_DELETE_TITLE = "永久刪除這筆記錄？"
_DELETE_CONSEQUENCE = "永久刪除，無法復原，也不再出現在列表與分析中。"
_DELETE_LABEL = ":red[永久刪除]"
_DELETED = "已刪除"
_SAVED = "已儲存修改。"
_NOTHING_CHANGED = "沒有變更。"
_OPEN_FAILED = "無法開啟這筆記錄，請稍後再試一次。"
_TITLE_REQUIRED = "請輸入標題"
_CATEGORY_REQUIRED = "請選擇或輸入分類"
_CATEGORY_TOO_LONG = f"分類不可超過 {_CATEGORY_MAX_LENGTH} 個字元"
_TIME_REQUIRED = "請選擇時間戳"
_FIX_FIELDS = "請修正標示的欄位。"
_CATEGORY_PLACEHOLDER = "選擇既有分類，或輸入新分類"
_CATEGORY_HINT = "輸入新分類後請按 Enter 或選取「Add」。"
_ERROR_FIELDS = ("title", "value", "category", "timestamp")

# Kept under the prefix forms.reset_interrupted_submits() clears: changing page closes the dialog.
_STATE_KEY = "__submit__record_dialog"
_OPENED_KEY = "__submit__record_dialog_opened"


@dataclass
class _Dialog:
    kind: str
    record: dict[str, Any]
    opened: int
    initial_time: datetime | None = None  # What the minute-precision time field was pre-filled with.
    mirror: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Outcome:
    saved: dict[str, Any] | None = None
    refused: api.ApiError | None = None


def _current() -> _Dialog | None:
    return st.session_state.get(_STATE_KEY)


def _submit_name(dialog: _Dialog) -> str:
    return f"record_{dialog.kind}_{dialog.opened}"


def _key(dialog: _Dialog, name: str) -> str:
    return f"record_edit_{name}_{dialog.opened}"


def _close() -> None:
    st.session_state.pop(_STATE_KEY, None)
    record_form.clear(_EDIT_PREFIX)


def _on_close_requested() -> None:
    """Cancel, X or Esc. A send under way or timed out may still take effect, so read the list again."""
    dialog = _current()
    if dialog is None:  # The send finished first and closed the dialog itself.
        return
    submit_key = f"__submit__{_submit_name(dialog)}"
    sent = submit_key in st.session_state or f"{submit_key}__errors" in st.session_state
    st.session_state.pop(submit_key, None)
    _close()
    if sent:
        records.request_reload(keep_selected_id=dialog.record["id"])


# --- Opening ---


def _rerun_list() -> None:
    """Stays a fragment rerun, so one failed check does not replace the page with SCREEN-04."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:  # Raised during a full run, where only a full rerun is allowed.
        st.rerun()


def _fetch_for_opening(record: dict[str, Any]) -> dict[str, Any]:
    """The record as it is now. Returns only when it can still be changed; otherwise explains and reruns."""
    state = records.list_state()
    try:
        fresh = api.get(f"/api/v1/records/{record['id']}", timeout=_OPEN_TIMEOUT_SECONDS)
    except api.BackendUnavailable:
        state.notice = _OPEN_FAILED
        _rerun_list()
    except api.ApiError as exc:
        if exc.status == 404:
            records.record_gone()
            st.rerun()
        if exc.status >= 500:
            state.notice = _OPEN_FAILED
            _rerun_list()
        records.request_reload(notice=exc.message, keep_selected_id=record["id"])
        st.rerun()
    user = session.current_user()
    assert user is not None
    allowed = records.availability(user, fresh)
    if not allowed.enabled:
        records.request_reload(notice=allowed.reason, keep_selected_id=record["id"])
        st.rerun()
    return fresh


def _open(kind: str, record: dict[str, Any]) -> None:
    fresh = _fetch_for_opening(record)
    records.refresh_options()
    opened = st.session_state.get(_OPENED_KEY, 0) + 1
    st.session_state[_OPENED_KEY] = opened
    dialog = _Dialog(kind, fresh, opened)
    if kind == _EDIT:
        record_form.clear(_EDIT_PREFIX)
        shown = records.to_widget_time(records.parse_timestamp(fresh["timestamp"]))
        assert shown is not None
        dialog.initial_time = shown.replace(second=0, microsecond=0)
        st.session_state[_key(dialog, "title")] = fresh["title"]
        st.session_state[_key(dialog, "value")] = records.format_number(float(fresh["value"]))
        st.session_state[_key(dialog, "category")] = fresh["category"]
        st.session_state[_key(dialog, "time")] = dialog.initial_time
    st.session_state[_STATE_KEY] = dialog
    records.list_state().notice = None
    st.rerun()


def open_edit(record: dict[str, Any]) -> None:
    _open(_EDIT, record)


def open_delete(record: dict[str, Any]) -> None:
    _open(_DELETE, record)


# --- After a send ---


def _finish(outcome: _Outcome, record_id: int, *, success_notice: str | None = None) -> None:
    refused = outcome.refused
    _close()
    if refused is None:
        records.request_reload(notice=success_notice, keep_selected_id=record_id if outcome.saved else None)
    elif refused.status == 404:
        records.record_gone()
    else:
        # A record can become anomalous after the list loaded: the row now shows why it is locked.
        records.request_reload(notice=refused.message, keep_selected_id=record_id)
    st.rerun()


def _refusal(exc: api.ApiError) -> _Outcome:
    """403 and 404 end the dialog. Anything else stays in it, with the input kept."""
    if exc.status in (403, 404):
        return _Outcome(refused=exc)
    raise exc


# --- SCREEN-12 ---


def _problems(title: str, value_text: str | None, category: str, chosen_time: datetime | None) -> list[dict[str, str]]:
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
    if chosen_time is None:
        problems.append({"field": "timestamp", "message": _TIME_REQUIRED})
    return problems


def _send_edit(dialog: _Dialog, values: dict[str, Any]) -> _Outcome | None:
    """Sends only what changed, which also keeps a resend from wiping the seconds of a timestamp.

    Returns None when nothing changed, because the backend refuses an empty update.
    """
    record = dialog.record
    title = (values[_key(dialog, "title")] or "").strip()
    value_text = values[_key(dialog, "value")]
    category = (values[_key(dialog, "category")] or "").strip()
    chosen_time = values[_key(dialog, "time")]
    if problems := _problems(title, value_text, category, chosen_time):
        raise api.ApiError(422, "VALIDATION_ERROR", _FIX_FIELDS, problems)
    value = record_form.parse_value(value_text)
    assert value is not None

    body: dict[str, Any] = {}
    if title != record["title"]:
        body["title"] = title
    if value != record["value"]:
        body["value"] = value
    if category != record["category"]:
        body["category"] = category
    if chosen_time != dialog.initial_time:
        zoned = records.from_widget_time(chosen_time)
        assert zoned is not None
        body["timestamp"] = zoned.isoformat()
    if not body:
        return None

    def patch(confirm: bool) -> dict[str, Any]:
        return api.patch(f"/api/v1/records/{record['id']}", json={**body, "confirm_anomaly": confirm})

    try:
        if "value" in body or "category" in body:
            ticked = values[record_form.confirm_key(_EDIT_PREFIX)]
            return _Outcome(saved=record_form.send_with_confirmation(_EDIT_PREFIX, category, value, ticked, patch))
        return _Outcome(saved=patch(False))
    except api.ApiError as exc:
        return _refusal(exc)


def _field_error(slot: Any, message: str | None) -> None:
    if message:
        slot.error(message)


def _required_error(submit: forms.FormSubmit, name: str, required_message: str, text: Any) -> str | None:
    """A required-field error goes away once the field is filled. Backend errors wait for the next send."""
    message = submit.field_error(name)
    filled = text.strip() if isinstance(text, str) else text
    return None if message == required_message and filled else message


def _value_field_error(submit: forms.FormSubmit, text: str | None) -> str | None:
    message = submit.field_error("value")
    if message in (record_form.VALUE_REQUIRED, record_form.VALUE_NOT_NUMBER):
        return record_form.value_error(text)
    return message


def _category_field_error(submit: forms.FormSubmit, text: str | None) -> str | None:
    message = _required_error(submit, "category", _CATEGORY_REQUIRED, text)
    return None if message == _CATEGORY_TOO_LONG and len((text or "").strip()) <= _CATEGORY_MAX_LENGTH else message


def _category_options(record: dict[str, Any], chosen: str | None) -> list[str]:
    options = list(records.list_state().categories)
    for name in (record["category"], chosen):
        if name and name not in options:
            options.append(name)
    return options


def _unit_and_range(category: str) -> str | None:
    """From what the list already loaded: the gate only asks the backend about a changed pair."""
    item = (records.list_state().display or {}).get(category)
    return record_form.range_text(item["range"], item["unit"]) if item else None


def _restore_after_outage(dialog: _Dialog) -> None:
    """SCREEN-04 replaces the page while the backend is down, which drops the widgets' values."""
    for key, value in dialog.mirror.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _draw_edit_body(dialog: _Dialog) -> None:
    if _current() is None:
        st.rerun()
    record = dialog.record
    title_key, value_key = _key(dialog, "title"), _key(dialog, "value")
    category_key, time_key = _key(dialog, "category"), _key(dialog, "time")
    widget_keys = [title_key, value_key, category_key, time_key]

    _restore_after_outage(dialog)
    submit = forms.FormSubmit(_submit_name(dialog), fields=[*widget_keys, record_form.confirm_key(_EDIT_PREFIX)])

    form_error = st.empty()
    title = st.text_input("標題", key=title_key, max_chars=_TITLE_MAX_LENGTH, disabled=submit.busy)
    title_error = st.empty()
    value_text = st.text_input("數值", key=value_key, live=_VALUE_PAUSE, disabled=submit.busy)
    value_error = st.empty()
    range_hint = st.empty()
    category = st.selectbox(
        "分類",
        _category_options(record, st.session_state.get(category_key)),
        index=None,
        key=category_key,
        placeholder=_CATEGORY_PLACEHOLDER,
        accept_new_options=True,
        disabled=submit.busy,
    )
    category_error = st.empty()
    st.caption(_CATEGORY_HINT)
    chosen_time = st.datetime_input("時間戳", value=None, key=time_key, step=60, format="YYYY-MM-DD", disabled=submit.busy)
    time_error = st.empty()
    # st.text, not Markdown: the creator's name is user text.
    st.text(f"目前時間戳：{records.format_timestamp(record['timestamp'])}（此欄位到分鐘；改動後秒數為 00）")
    st.text(f"創建者：{record['creator_name'] or '—'}（固定，不能更改）。不保留修改紀錄。")

    # The warning is about a change: an unchanged pair is not judged again by the backend.
    name = (category or "").strip()
    value = record_form.parse_value(value_text)
    pair_changed = bool(name) and value is not None and (name, value) != (record["category"], record["value"])
    gate = record_form.draw_anomaly_gate(
        _EDIT_PREFIX, name if pair_changed else None, value if pair_changed else None, busy=submit.busy
    )

    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button("取消", key="record_edit_cancel", disabled=submit.busy, on_click=_on_close_requested)
        st.button(
            submit.label("儲存"),
            key="record_edit_save",
            type="primary",
            disabled=submit.busy or gate.blocks_submit,
            on_click=submit.begin,
        )

    _field_error(title_error, _required_error(submit, "title", _TITLE_REQUIRED, title))
    _field_error(value_error, _value_field_error(submit, value_text))
    _field_error(category_error, _category_field_error(submit, category))
    _field_error(time_error, _required_error(submit, "timestamp", _TIME_REQUIRED, chosen_time))

    hint = gate.range_text if gate.checked else _unit_and_range(name)
    if hint:
        range_hint.caption(records.escape_markdown(hint))

    has_field_error = any(submit.field_error(item) for item in _ERROR_FIELDS)
    if not gate.conflict and not has_field_error:
        with form_error:
            submit.show_form_error()

    if not submit.busy:
        dialog.mirror = {key: st.session_state.get(key) for key in widget_keys}

    def done(outcome: _Outcome | None) -> None:
        if outcome is None:
            _close()
            records.list_state().notice = _NOTHING_CHANGED
            st.rerun()
        note = record_form.saved_note(_EDIT_PREFIX, outcome.saved) if outcome.saved else None
        _finish(outcome, record["id"], success_notice=f"{_SAVED}{note or ''}")

    submit.run(lambda: _send_edit(dialog, submit.values), on_success=done)


# --- SCREEN-13 ---


def _send_delete(dialog: _Dialog) -> _Outcome:
    try:
        api.delete(f"/api/v1/records/{dialog.record['id']}")
    except api.ApiError as exc:
        return _refusal(exc)
    return _Outcome()


def _draw_delete_body(dialog: _Dialog) -> None:
    if _current() is None:
        st.rerun()
    record = dialog.record
    submit = forms.FormSubmit(_submit_name(dialog), fields=[])

    with st.empty():
        submit.show_form_error()
    with st.container(border=True):
        # st.text, not Markdown: the title and category are user text.
        st.text(record["title"])
        item = (records.list_state().display or {}).get(record["category"])
        value = records.format_value(float(record["value"]), item["unit"] if item else None)
        st.text(f"{record['category']}　{value}　{records.format_timestamp(record['timestamp'])}")
    st.text(_DELETE_CONSEQUENCE)
    with st.container(horizontal=True, horizontal_alignment="right"):
        # Stays usable while deleting (M2 section 3). It cannot recall a request already sent, so
        # closing this way reads the list again and shows what actually happened.
        st.button("取消", key="record_delete_cancel", on_click=_on_close_requested)
        st.button(
            submit.label(_DELETE_LABEL),
            key="record_delete_confirm",
            disabled=submit.busy,
            on_click=submit.begin,
        )

    submit.run(
        lambda: _send_delete(dialog),
        on_success=lambda outcome: _finish(outcome, record["id"], success_notice=_DELETED),
    )


# --- Drawing ---


def draw_open_dialog() -> None:
    """Call once at the end of the page, outside the list's fragment. Only one dialog can open per run."""
    dialog = _current()
    if dialog is None:
        return

    if dialog.kind == _EDIT:

        @st.dialog(_EDIT_TITLE, width=_DIALOG_WIDTH, on_dismiss=_on_close_requested)
        def _edit_dialog() -> None:
            _draw_edit_body(dialog)

        _edit_dialog()
    else:

        @st.dialog(_DELETE_TITLE, width=_DIALOG_WIDTH, on_dismiss=_on_close_requested)
        def _delete_dialog() -> None:
            _draw_delete_body(dialog)

        _delete_dialog()
