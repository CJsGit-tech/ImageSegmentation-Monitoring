"""SCREEN-02: get the password right the first time. The rules stay visible and failures name what is missing."""

import re

import streamlit as st

from core import api, forms, nav

_USERNAME = "register_username"
_PASSWORD = "register_password"
_CONFIRM = "register_confirm_password"
_RESULT = "register_result"
_REGISTERED = "register_registered_username"
# login.py's account field. A page script cannot be imported, because importing would run it.
_LOGIN_USERNAME = "login_username"
_CARD_WIDTH = 560

_USERNAME_HINT = "2 至 64 個字元，可用文字、數字與 _ . - @，不可有空白"
_DUPLICATE_BANNER = "此帳號已存在，請改用其他帳號。"
_DUPLICATE_FIELD = "此帳號已存在"
_REJECTED_BANNER = "註冊未完成，請依下方標示修正。密碼已清空，請重新輸入。"
_GENERIC_BANNER = "註冊失敗，請稍後再試一次。"
_USERNAME_UNKNOWN = "帳號格式不正確，請確認後再試一次"
_PASSWORD_UNKNOWN = "密碼不符合要求，請重新輸入"

# Keyed by the backend's problem type. The backend decides; these checks only describe a password
# that was not sent because another field stopped the submit. Keep them the same as validation.py.
_RULES = {
    "password_too_short": ("至少 8 個字元", lambda password: len(password) >= 8),
    "password_missing_uppercase": ("包含英文大寫", lambda password: any("A" <= c <= "Z" for c in password)),
    "password_missing_lowercase": ("包含英文小寫", lambda password: any("a" <= c <= "z" for c in password)),
    "password_missing_digit": ("包含數字", lambda password: any("0" <= c <= "9" for c in password)),
    "password_missing_symbol": ("包含符號", lambda password: any(not c.isalnum() and not c.isspace() for c in password)),
}
_USERNAME_ERRORS = {
    "username_too_short": "帳號至少需要 2 個字元",
    "username_too_long": "帳號最多 64 個字元",
    "username_invalid_characters": "帳號只能包含文字、數字與 _ . - @，不可有空白",
}
_PASSWORD_ERRORS = {"password_too_long": "密碼最多 128 個字元"}

submit = forms.FormSubmit("register", fields=[_USERNAME, _PASSWORD, _CONFIRM], clear_on_failure=[_PASSWORD, _CONFIRM])


def _begin_if_valid() -> None:
    """Nothing is sent when this finds a problem. A mismatch clears both passwords; a blank field keeps what was typed."""
    username = st.session_state.get(_USERNAME, "")
    password = st.session_state.get(_PASSWORD, "")
    confirm = st.session_state.get(_CONFIRM, "")
    errors = {}
    if not username.strip():
        errors[_USERNAME] = "請輸入帳號"
    if not password:
        errors[_PASSWORD] = "請輸入密碼"
    if not confirm:
        errors[_CONFIRM] = "請再次輸入密碼"
    elif password and password != confirm:
        errors[_CONFIRM] = "兩次輸入的密碼不一致，請重新輸入密碼與確認密碼"
    if not errors:
        submit.begin()
        return
    missing = {rule for rule, (_label, met) in _RULES.items() if not met(password)} if password else None
    st.session_state[_RESULT] = {"fields": errors, "missing": missing, "local": True}
    if password and confirm and password != confirm:
        st.session_state[_PASSWORD] = ""
        st.session_state[_CONFIRM] = ""


def _record_rejection(error: api.ApiError) -> str:
    """Keeps the problems for the next run and returns the overall message. Backend wording is never shown."""
    if error.status == 409 and isinstance(error.details, dict) and error.details.get("field") == "username":
        # The password rules are checked first, so reaching the duplicate check means they all passed.
        st.session_state[_RESULT] = {"fields": {_USERNAME: _DUPLICATE_FIELD}, "missing": set()}
        return _DUPLICATE_BANNER
    if error.status != 422 or not isinstance(error.details, list):
        return _GENERIC_BANNER

    fields: dict[str, str] = {}
    missing: set[str] = set()
    for problem in error.details:
        if not isinstance(problem, dict):
            continue
        field = str(problem.get("field", "")).rsplit(".", 1)[-1]
        problem_type = problem.get("type")
        if problem_type in _RULES:
            missing.add(problem_type)
        elif field == "username":
            fields.setdefault(_USERNAME, _USERNAME_ERRORS.get(problem_type, _USERNAME_UNKNOWN))
        elif field == "password":
            fields.setdefault(_PASSWORD, _PASSWORD_ERRORS.get(problem_type, _PASSWORD_UNKNOWN))
    if not fields and not missing:
        return _GENERIC_BANNER
    st.session_state[_RESULT] = {"fields": fields, "missing": missing}
    return _REJECTED_BANNER


def _register() -> dict:
    try:
        return api.send(
            "POST",
            "/api/v1/auth/register",
            json={"username": submit.values[_USERNAME], "password": submit.values[_PASSWORD]},
        )
    except api.ApiError as exc:
        raise api.ApiError(exc.status, exc.code, _record_rejection(exc)) from exc


def _on_registered(user: dict) -> None:
    st.session_state[_REGISTERED] = user["username"]
    st.rerun()


def _field_error(errors: dict[str, str], field: str) -> None:
    if field in errors:
        # Not st.caption: its reduced opacity leaves red text too faint to read comfortably.
        st.markdown(f":red[:material/error: {errors[field]}]")


def _rule_line(rule: str, label: str, missing: set[str] | None) -> str:
    if missing is None:
        return f":gray[:material/radio_button_unchecked:] {label}"
    if rule in missing:
        return f":red[:material/close: **{label}**]"
    return f":material/check: {label}"


def _success(username: str) -> None:
    st.success("註冊成功。新帳號為 Viewer（唯讀），需要更高權限請洽管理者。")
    # An account such as __a__ is valid and must not turn into markdown formatting.
    shown = re.sub(r"[_.@-]", lambda match: f"\\{match.group()}", username)
    st.markdown(f"帳號 **{shown}** 已預填，請登入。")
    if st.button("前往登入", key="register_go_to_login", type="primary", width="stretch"):
        st.session_state[_LOGIN_USERNAME] = st.session_state.pop(_REGISTERED)
        st.switch_page(nav.login_page())


def _form() -> None:
    result = st.session_state.pop(_RESULT, {})
    errors = result.get("fields", {})
    # A local stop did not reach FormSubmit, so its message from an earlier attempt is out of date.
    if not result.get("local"):
        submit.show_form_error()

    with st.form("register_form", border=False):
        st.text_input("帳號", key=_USERNAME, autocomplete="username", disabled=submit.busy)
        _field_error(errors, _USERNAME)
        st.caption(_USERNAME_HINT)
        st.text_input("密碼", type="password", key=_PASSWORD, autocomplete="new-password", disabled=submit.busy)
        _field_error(errors, _PASSWORD)
        st.markdown(
            "  \n".join(_rule_line(rule, label, result.get("missing")) for rule, (label, _met) in _RULES.items())
        )
        st.text_input("確認密碼", type="password", key=_CONFIRM, autocomplete="new-password", disabled=submit.busy)
        _field_error(errors, _CONFIRM)
        st.form_submit_button(
            submit.label("註冊"),
            key="register_submit",
            type="primary",
            icon="spinner" if submit.busy else None,
            width="stretch",
            disabled=submit.busy,
            on_click=_begin_if_valid,
        )

    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.caption("已有帳號？", width="content")
        st.page_link(nav.login_page(), label="登入", width="content")


with st.container(horizontal_alignment="center"):
    with st.container(border=True, width=_CARD_WIDTH):
        st.title("註冊", anchor=False)
        registered = st.session_state.get(_REGISTERED)
        if registered:
            _success(registered)
        else:
            _form()

if not registered:
    submit.run(_register, on_success=_on_registered)
