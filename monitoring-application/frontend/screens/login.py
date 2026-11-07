"""SCREEN-01: the shortest way in, explaining why the user landed here when there is a reason."""

import streamlit as st

from core import api, forms, nav, session

_USERNAME = "login_username"
_PASSWORD = "login_password"
_FIELD_ERRORS = "login_field_errors"
_CARD_WIDTH = 560
_INVALID_CREDENTIALS = "帳號或密碼錯誤"

_NOTICES = {
    "logged_out": "你已登出。",
    "logged_out_local": "已在此瀏覽器登出。但系統暫時沒有回應，這次登入在系統端要到過期才會失效。",
    "expired": "登入已過期，請重新登入。",
    "superseded": "此帳號已在別處登入，請重新登入。",
}

submit = forms.FormSubmit("login", fields=[_USERNAME, _PASSWORD], clear_on_failure=[_PASSWORD])


def _begin_if_complete() -> None:
    """A blank field stops here: nothing was sent, so what was typed stays, including the password."""
    errors = {}
    if not st.session_state.get(_USERNAME, "").strip():
        errors[_USERNAME] = "請輸入帳號"
    if not st.session_state.get(_PASSWORD, ""):
        errors[_PASSWORD] = "請輸入密碼"
    if errors:
        st.session_state[_FIELD_ERRORS] = errors
        return
    session.clear_notice()
    submit.begin()


def _login() -> session.CurrentUser:
    try:
        return session.login(submit.values[_USERNAME], submit.values[_PASSWORD])
    except api.ApiError as exc:
        # Input the backend rejects as malformed cannot match an account. Its details are English.
        if exc.status == 422:
            raise api.ApiError(exc.status, exc.code, _INVALID_CREDENTIALS) from exc
        raise


field_errors = st.session_state.pop(_FIELD_ERRORS, {})


def _field_error(field: str) -> None:
    if field in field_errors:
        # Not st.caption: its reduced opacity leaves red text too faint to read comfortably.
        st.markdown(f":red[:material/error: {field_errors[field]}]")


with st.container(horizontal_alignment="center"):
    with st.container(border=True, width=_CARD_WIDTH):
        st.title("登入", anchor=False)
        st.caption("MonitoringDashboard　即時資料分析與監控")

        notice = session.notice()
        if notice:
            st.info(_NOTICES[notice])
        if not field_errors:
            submit.show_form_error()

        with st.form("login_form", border=False):
            st.text_input("帳號", key=_USERNAME, autocomplete="username", disabled=submit.busy)
            _field_error(_USERNAME)
            st.text_input(
                "密碼",
                type="password",
                key=_PASSWORD,
                autocomplete="current-password",
                disabled=submit.busy,
            )
            _field_error(_PASSWORD)
            st.form_submit_button(
                submit.label("登入"),
                key="login_submit",
                type="primary",
                icon="spinner" if submit.busy else None,
                width="stretch",
                disabled=submit.busy,
                on_click=_begin_if_complete,
            )

        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            st.caption("還沒有帳號？", width="content")
            st.page_link(nav.register_page(), label="註冊", width="content")

submit.run(_login, on_success=lambda _user: st.switch_page(nav.home_page()))
