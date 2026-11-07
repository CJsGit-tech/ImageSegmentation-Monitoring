"""SHARED-02 form submit: truly disabled while sending, errors kept, passwords cleared."""

from collections.abc import Callable, Iterable
from typing import Any

import streamlit as st

from core import api

_REQUESTED = "requested"
_RUNNING = "running"
_STATE_PREFIX = "__submit__"


class FormSubmit:
    """Create before the form's widgets. List every field's widget key in `fields`.

    A widget rendered with disabled=True loses its value in Streamlit 1.67, so this class
    snapshots the values on submit and puts them back. Read them from `submit.values`.

    submit = FormSubmit("login", fields=["login_username", "login_password"], clear_on_failure=["login_password"])
    submit.show_form_error()
    with st.form("login_form"):
        st.text_input("帳號", key="login_username", disabled=submit.busy)
        ...
        st.form_submit_button(submit.label("登入"), key="login_submit", disabled=submit.busy, on_click=submit.begin)
    submit.run(lambda: send(submit.values["login_username"]), on_success=...)
    """

    def __init__(self, name: str, *, fields: Iterable[str], clear_on_failure: Iterable[str] = ()):
        self._key = f"{_STATE_PREFIX}{name}"
        self._values_key = f"{self._key}__values"
        self._errors_key = f"{self._key}__errors"
        self._fields = tuple(fields)
        self._clear_on_failure = tuple(clear_on_failure)
        self.values: dict[str, Any] = st.session_state.get(self._values_key, {})
        st.session_state.update(self.values)
        if not self.busy:
            st.session_state.pop(self._values_key, None)

    @property
    def busy(self) -> bool:
        return self._key in st.session_state

    def begin(self) -> None:
        """on_click for the submit button: the next run renders everything disabled."""
        st.session_state.pop(self._errors_key, None)
        st.session_state[self._values_key] = {field: st.session_state.get(field) for field in self._fields}
        st.session_state[self._key] = _REQUESTED

    def label(self, idle_label: str) -> str:
        return "處理中…" if self.busy else idle_label

    def _errors(self) -> dict[str, Any]:
        return st.session_state.get(self._errors_key, {})

    def show_form_error(self) -> None:
        message = self._errors().get("form")
        if message:
            st.error(message)

    def field_error(self, field: str) -> str | None:
        return self._errors().get("fields", {}).get(field)

    def run(self, action: Callable[[], Any], *, on_success: Callable[[Any], None] | None = None) -> None:
        """Send once, after the disabled form has rendered. Call last in the page."""
        state = st.session_state.get(self._key)
        if state is None:
            return
        if state == _RUNNING:
            # A rerun interrupted the send. It may have taken effect, so it is not repeated.
            self._fail({"form": "送出已中斷，請確認結果後再試一次。"})
        st.session_state[self._key] = _RUNNING

        try:
            result = action()
        except api.BackendTimeout:
            self._fail({"form": "系統回應逾時，請再試一次。"})
        except api.BackendUnavailable:
            self._fail({"form": "無法連到系統，請稍後再試一次。"})
        except api.ApiError as exc:
            self._fail({"form": exc.message, "fields": exc.field_errors()})
        st.session_state.pop(self._key, None)
        st.session_state.pop(self._values_key, None)
        if on_success:
            on_success(result)

    def _fail(self, errors: dict[str, Any]) -> None:
        values = st.session_state[self._values_key]
        values.update(dict.fromkeys(self._clear_on_failure, ""))
        st.session_state.pop(self._key, None)
        st.session_state[self._errors_key] = errors
        st.rerun()


def reset_interrupted_submits() -> None:
    """Called when the page changes: nothing from a submit left behind resumes on return."""
    for key in [key for key in st.session_state if str(key).startswith(_STATE_PREFIX)]:
        del st.session_state[key]
