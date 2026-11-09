"""The pre-save anomaly warning (PRD-18-R6), shared by SCREEN-11 and SCREEN-12.

GET /records/anomaly-check is the only source of the rule, and the backend's judgement at save
time is final: an early click or a changed range gets a 409 with nothing written. A tick applies
to one exact (category, value) pair, so a changed value can never carry an old confirmation.

Use plain widgets, not st.form: a form does not rerun until submit, so it could neither warn nor
disable its submit button. Inside the caller's FormSubmit flow:

    submit = forms.FormSubmit(name, fields=[..., record_form.confirm_key(prefix)])
    ...widgets...
    gate = record_form.draw_anomaly_gate(prefix, category, value, busy=submit.busy)
    st.button(submit.label("送出"), disabled=submit.busy or gate.blocks_submit, on_click=submit.begin)
    if not gate.conflict:
        submit.show_form_error()   # A 409 is explained by the warning, not by a generic error.
    submit.run(lambda: record_form.send_with_confirmation(
        prefix, category, value, submit.values[record_form.confirm_key(prefix)],
        lambda confirm: api.patch(path, json={..., "confirm_anomaly": confirm}),
    ), on_success=...)

The caller handles its own success, 403 and 404. saved_note() words the outcome from the response.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import streamlit as st

from core import api, records

ANOMALY_WARNING = "儲存後將標為異常"
VALUE_REQUIRED = "請輸入數值"
VALUE_NOT_NUMBER = "數值必須是數字"
CONFIRMATION_REQUIRED_CODE = "ANOMALY_CONFIRMATION_REQUIRED"

_CONFIRM_LABEL = "我了解，確認儲存為異常記錄"
_RANGE_CHANGED = "此分類的範圍剛被變更，依目前範圍這筆資料會成為異常。"
_CHECK_FAILED = "暫時無法確認此分類的範圍，送出時系統仍會檢查。"
_SAVED_ANOMALY = "這筆記錄已標為異常。"
_SAVED_NORMAL_AFTER_CONFIRM = "範圍已變更，依目前範圍這筆記錄未被標為異常。"
_CHECK_TIMEOUT_SECONDS = 5
# ASCII only: float() alone would also accept 1_000, full-width digits, nan and inf.
_NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", re.ASCII)

Pair = tuple[str, float]


@dataclass(frozen=True)
class AnomalyGate:
    blocks_submit: bool
    checked: bool  # The check answered for this pair, so range_text is current even when None.
    range_text: str | None  # Plain text: escape before any Markdown element.
    conflict: bool  # The last send ended in a 409.


@dataclass
class _State:
    bound: Pair | None = None
    check: dict[str, Any] | None = None
    check_pair: Pair | None = None
    conflict_pair: Pair | None = None
    conflict_range: dict[str, float] | None = None
    range_changed: bool = False
    last_send_conflict: bool = False
    last_send_confirmed: bool = False


def _state(prefix: str) -> _State:
    # Under the prefix forms.reset_interrupted_submits() clears, so changing page starts over.
    return st.session_state.setdefault(f"__submit__record_form_{prefix}", _State())


def confirm_key(prefix: str) -> str:
    return f"{prefix}_anomaly_confirm"


def clear(prefix: str) -> None:
    st.session_state.pop(f"__submit__record_form_{prefix}", None)
    st.session_state.pop(confirm_key(prefix), None)


def parse_value(text: str | None) -> float | None:
    """The number a value field holds, or None when it is blank or not a finite number."""
    stripped = (text or "").strip()
    if not _NUMBER.fullmatch(stripped):
        return None
    number = float(stripped)
    return number if math.isfinite(number) else None


def value_error(text: str | None) -> str | None:
    if not (text or "").strip():
        return VALUE_REQUIRED
    return None if parse_value(text) is not None else VALUE_NOT_NUMBER


def range_text(bounds: dict[str, float] | None, unit: str | None) -> str | None:
    if bounds:
        low = records.format_number(float(bounds["min"]))
        high = records.format_number(float(bounds["max"]))
        return f"範圍 {low} – {high} {unit or ''}".rstrip() + "（邊界含在異常內）"
    return f"單位：{unit}" if unit else None


def _pair(category: str | None, value: float | None) -> Pair | None:
    name = (category or "").strip()
    return (name, value) if name and value is not None else None


def _run_check(state: _State, pair: Pair | None) -> bool:
    """Ask the backend. Returns False when it could not answer, which leaves the save to decide."""
    state.check, state.check_pair = None, None
    if pair is None:
        return True
    try:
        state.check = api.get(
            "/api/v1/records/anomaly-check",
            params={"category": pair[0], "value": pair[1]},
            timeout=_CHECK_TIMEOUT_SECONDS,
        )
    except api.BackendUnavailable:
        return False
    except api.ApiError as exc:
        # A 422 means the text itself is invalid, and the save reports that at its field.
        return exc.status < 500
    state.check_pair = pair
    return True


def _draw_warning(prefix: str, state: _State, pair: Pair, *, busy: bool) -> bool:
    """Returns whether the box is ticked."""
    key = confirm_key(prefix)
    if not busy:
        if state.bound != pair:
            st.session_state[key] = False
            state.bound = pair
        elif st.session_state.get(key) is None:
            st.session_state[key] = False
    if state.range_changed and state.conflict_pair == pair:
        st.caption(_RANGE_CHANGED)
    st.error(f"**{ANOMALY_WARNING}**。請勾選確認後才能送出。")
    return st.checkbox(_CONFIRM_LABEL, key=key, disabled=busy)


def draw_anomaly_gate(prefix: str, category: str | None, value: float | None, *, busy: bool) -> AnomalyGate:
    """Check the pair and draw the warning and checkbox, or nothing, in a slot that always exists.

    While sending there is no new check: the result already on screen stays, and bound stays
    too, because send_with_confirmation compares what was submitted with what was confirmed.
    """
    state = _state(prefix)
    pair = _pair(category, value)
    answered = busy or _run_check(state, pair)
    check = state.check if pair is not None and state.check_pair == pair else None

    if check is not None and not check["would_be_anomaly"]:
        state.conflict_pair, state.conflict_range, state.range_changed = None, None, False
    in_conflict = pair is not None and state.conflict_pair == pair
    warn = bool(check["would_be_anomaly"]) if check is not None else (in_conflict and not busy)
    if busy and in_conflict:
        warn = state.bound == pair

    slot = st.container()
    ticked = False
    if warn and pair is not None:
        with slot:
            ticked = _draw_warning(prefix, state, pair, busy=busy)
    elif not busy:
        state.bound = None
        if pair is not None and not answered:
            slot.caption(_CHECK_FAILED)

    if check is not None:
        text = range_text(check["range"], check["unit"])
    else:
        text = range_text(state.conflict_range, None) if in_conflict else None
    return AnomalyGate(
        blocks_submit=warn and not ticked,
        checked=check is not None or in_conflict,
        range_text=text,
        conflict=state.last_send_conflict,
    )


def send_with_confirmation(
    prefix: str,
    category: str,
    value: float,
    ticked: bool | None,
    send: Callable[[bool], Any],
) -> Any:
    """Call send(confirm_anomaly). A tick counts only for the exact pair it was given for.

    A 409 is recorded and raised again, so that FormSubmit keeps the input and the next run
    shows the warning, using the 409's own range if the check cannot answer.
    """
    state = _state(prefix)
    pair = (category.strip(), value)
    confirm = bool(ticked) and state.bound == pair
    state.last_send_confirmed = confirm
    state.last_send_conflict = False
    try:
        return send(confirm)
    except api.ApiError as exc:
        if exc.code == CONFIRMATION_REQUIRED_CODE:
            details = exc.details if isinstance(exc.details, dict) else {}
            state.last_send_conflict = True
            state.conflict_pair = pair
            state.conflict_range = details.get("range")
            # Only a clean answer for this same pair means the range moved. Otherwise the
            # person simply clicked before the check had run.
            state.range_changed = state.check_pair == pair and bool(state.check) and not state.check["would_be_anomaly"]
        raise


def saved_note(prefix: str, record: dict[str, Any]) -> str | None:
    """The outcome in words, from the response and never from the earlier check."""
    if record["is_anomaly"]:
        return _SAVED_ANOMALY
    return _SAVED_NORMAL_AFTER_CONFIRM if _state(prefix).last_send_confirmed else None
