"""SCREEN-40: Admin lists users, searches by account and changes a user's role (PRD-13).

The list is one fragment, like SCREEN-10: searching and paging do not re-run the shell's login
check. The change-role dialog is drawn at the end of the page, outside the fragment, for as long
as its state exists, because a failed send reruns the whole app (FormSubmit).

There are no disable or delete controls (PRD-13-R6). The Admin's own row cannot be changed
(PRD-13-R5); the backend enforces that too.
"""

import math
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

from core import api, forms
from core.records import display_zone, escape_markdown, format_timestamp

PAGE_SIZE = 20
ROLES = ("Admin", "User", "Viewer")
_SEARCH_MAX_LENGTH = 64  # The backend's USERNAME_MAX_LENGTH.
_OPEN_TIMEOUT_SECONDS = 10
_SKELETON_ROWS = 6
_SKELETON_ROW_HEIGHT = 32

_LOAD_ERROR = "無法載入使用者，已保留你的搜尋條件。"
_OPEN_FAILED = "無法開啟變更角色，請稍後再試一次。"
_USER_GONE = "這位使用者已不存在"
_SELF_REASON = "不能變更自己的角色"
_TAKES_EFFECT = "將於該使用者下一次操作時生效，不需重新登入。"
_SAME_ROLE = "與目前角色相同，請選擇其他角色。"
_NO_LOGIN = "—"

# Kept under the prefix forms.reset_interrupted_submits() clears: changing page resets the screen.
_STATE_KEY = "__submit__admin_users_list"
_DIALOG_KEY = "__submit__admin_users_dialog"
_OPENED_KEY = "__submit__admin_users_dialog_opened"
_SEARCH = "admin_users_search"

_COLUMNS = {
    "帳號": st.column_config.TextColumn("帳號", width="medium"),
    "角色": st.column_config.TextColumn("角色", width="small"),
    "註冊時間": st.column_config.TextColumn("註冊時間", width="medium"),
    "最後登入時間": st.column_config.TextColumn("最後登入時間", width="medium"),
}


# --- List state ---


@dataclass(frozen=True)
class _Request:
    """One page to load. It only becomes the current page when the backend answers."""

    q: str | None = None
    stack: tuple[str | None, ...] = (None,)
    keep_selected_id: int | None = None


@dataclass
class _ListState:
    q: str | None = None
    stack: tuple[str | None, ...] = (None,)
    page: dict[str, Any] | None = None
    pending: _Request | None = field(default_factory=_Request)
    failed: _Request | None = None
    error: str | None = None
    notice: str | None = None
    notice_is_success: bool = False
    generation: int = 0
    selected_id: int | None = None


def _state() -> _ListState:
    return st.session_state.setdefault(_STATE_KEY, _ListState())


def _start(request: _Request, notice: str | None = None, *, success: bool = False) -> None:
    state = _state()
    state.pending = request
    state.failed = None
    state.error = None
    state.notice = notice
    state.notice_is_success = success


def _params(q: str | None, cursor: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": PAGE_SIZE}
    if q:
        params["q"] = q
    if cursor:
        params["cursor"] = cursor
    return params


def _request_search() -> None:
    _start(_Request((st.session_state.get(_SEARCH) or "").strip() or None))


def _request_previous() -> None:
    state = _state()
    if len(state.stack) > 1:
        _start(_Request(state.q, state.stack[:-1]))


def _request_next() -> None:
    state = _state()
    cursor = state.page["next_cursor"] if state.page else None
    if cursor:
        _start(_Request(state.q, (*state.stack, cursor)))


def _request_retry() -> None:
    state = _state()
    if state.failed:
        _start(state.failed)


def _request_reload(notice: str | None = None, *, keep_selected_id: int | None = None, success: bool = False) -> None:
    """The same search and page again. For use after a change."""
    state = _state()
    _start(_Request(state.q, state.stack, keep_selected_id), notice, success=success)


def _commit(state: _ListState, request: _Request, page: dict[str, Any]) -> None:
    state.q = request.q
    state.stack = request.stack
    state.page = page
    state.pending = None
    state.selected_id = request.keep_selected_id
    state.generation += 1


def _run_pending() -> None:
    """Send the pending request. Called with the waiting view already drawn."""
    state = _state()
    while (request := state.pending) is not None:
        try:
            page = api.get("/api/v1/users", params=_params(request.q, request.stack[-1]))
        except api.BackendUnavailable:
            state.pending, state.failed, state.error = None, request, _LOAD_ERROR
            return
        except api.ApiError as exc:
            fields = exc.field_errors()
            if exc.status == 422 and "cursor" in fields:
                state.pending, state.notice = replace(request, stack=(None,)), fields["cursor"]
                continue
            state.pending, state.failed = None, request
            state.error = _LOAD_ERROR if exc.status >= 500 else exc.message
            if exc.status == 403:
                # This Admin lost the role: a full run lets the shell show SCREEN-04.
                st.rerun(scope="app")
            return
        if not page["items"] and page["total"] > 0 and len(request.stack) > 1:
            state.pending = replace(request, stack=request.stack[:-1])
            continue
        _commit(state, request, page)


def _total_pages(state: _ListState) -> int:
    assert state.page is not None
    return max(math.ceil(state.page["total"] / state.page["limit"]), len(state.stack), 1)


def _rerun_list() -> None:
    """A fragment rerun stays one, so it skips the shell's login check."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:  # Raised during a full run, where only a full rerun is allowed.
        st.rerun()


# --- Dialog state ---


@dataclass
class _Dialog:
    user: dict[str, Any]
    opened: int


@dataclass(frozen=True)
class _Outcome:
    saved: dict[str, Any] | None = None
    refused: api.ApiError | None = None


def _current() -> _Dialog | None:
    return st.session_state.get(_DIALOG_KEY)


def _submit_name(dialog: _Dialog) -> str:
    return f"admin_users_role_{dialog.opened}"


def _role_key(dialog: _Dialog) -> str:
    return f"admin_users_new_role_{dialog.opened}"


def _close() -> None:
    dialog = st.session_state.pop(_DIALOG_KEY, None)
    if dialog is not None:
        st.session_state.pop(_role_key(dialog), None)


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
        _request_reload(keep_selected_id=dialog.user["id"])


def _open(user: dict[str, Any]) -> None:
    """Read the page again first: the user's role may have changed since the list loaded."""
    state = _state()
    request = _Request(state.q, state.stack, user["id"])
    try:
        page = api.get(
            "/api/v1/users", params=_params(state.q, state.stack[-1]), timeout=_OPEN_TIMEOUT_SECONDS
        )
    except api.BackendUnavailable:
        state.notice, state.notice_is_success = _OPEN_FAILED, False
        _rerun_list()
    except api.ApiError as exc:
        if exc.status == 403:
            st.rerun(scope="app")
        if exc.status >= 500:
            state.notice, state.notice_is_success = _OPEN_FAILED, False
            _rerun_list()
        _request_reload(exc.message, keep_selected_id=user["id"])
        _rerun_list()
    fresh = next((item for item in page["items"] if item["id"] == user["id"]), None)
    if fresh is None:
        _request_reload(_USER_GONE)
        _rerun_list()
    _commit(state, request, page)
    state.notice, state.notice_is_success = None, False
    if fresh["is_self"]:
        _rerun_list()
    if fresh["role"] != user["role"]:
        state.notice = f"這位使用者的角色已被變更為 {fresh['role']}。"
    opened = st.session_state.get(_OPENED_KEY, 0) + 1
    st.session_state[_OPENED_KEY] = opened
    st.session_state[_DIALOG_KEY] = _Dialog(fresh, opened)
    st.rerun()


def _send(dialog: _Dialog, role: str | None) -> _Outcome:
    if role not in ROLES:
        raise api.ApiError(422, "VALIDATION_ERROR", "請選擇新角色。")
    try:
        return _Outcome(saved=api.patch(f"/api/v1/users/{dialog.user['id']}/role", json={"role": role}))
    except api.ApiError as exc:
        # 403 and 404 end the dialog. Anything else stays in it, with the choice kept.
        if exc.status in (403, 404):
            return _Outcome(refused=exc)
        raise


def _finish(dialog: _Dialog, outcome: _Outcome) -> None:
    _close()
    refused = outcome.refused
    if outcome.saved is not None:
        saved = outcome.saved
        _request_reload(
            f"已將 {saved['username']} 的角色變更為 {saved['role']}。",
            keep_selected_id=saved["id"],
            success=True,
        )
    elif refused is not None and refused.status == 404:
        _request_reload(refused.message)
    elif refused is not None and refused.code == "FORBIDDEN":
        st.rerun(scope="app")
    elif refused is not None:
        _request_reload(refused.message, keep_selected_id=dialog.user["id"])
    st.rerun()


def _draw_dialog_body(dialog: _Dialog) -> None:
    if _current() is None:
        st.rerun()
    user = dialog.user
    role_key = _role_key(dialog)
    submit = forms.FormSubmit(_submit_name(dialog), fields=[role_key])

    error_slot = st.empty()
    summary_slot = st.empty()
    chosen = st.selectbox(
        "新角色",
        ROLES,
        index=None,
        key=role_key,
        placeholder="選擇新角色",
        disabled=submit.busy,
    )
    same_slot = st.empty()
    st.text(_TAKES_EFFECT)
    with st.container(horizontal=True, horizontal_alignment="right"):
        # Stays usable while sending (M5 section 3). Closing after a send reads the list again.
        st.button("取消", key="admin_users_cancel", on_click=_on_close_requested)
        st.button(
            submit.label("確認變更"),
            key="admin_users_confirm",
            type="primary",
            disabled=submit.busy or chosen is None or chosen == user["role"],
            on_click=submit.begin,
        )

    # st.text, not Markdown: the account name is user text.
    summary_slot.text(f"{user['username']}：{user['role']} → {chosen or '（請選擇）'}")
    if chosen == user["role"]:
        same_slot.caption(_SAME_ROLE)
    with error_slot:
        submit.show_form_error()

    submit.run(lambda: _send(dialog, submit.values.get(role_key)), on_success=lambda outcome: _finish(dialog, outcome))


def _draw_open_dialog() -> None:
    """Call once at the end of the page, outside the list's fragment."""
    dialog = _current()
    if dialog is None:
        return

    @st.dialog("變更角色", on_dismiss=_on_close_requested)
    def _role_dialog() -> None:
        _draw_dialog_body(dialog)

    _role_dialog()


# --- Drawing the list ---


def _draw_form(state: _ListState) -> None:
    st.session_state.setdefault(_SEARCH, (state.pending.q if state.pending else state.q) or "")
    with st.form("admin_users_search_form", border=True):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            with st.container(width=260):
                st.text_input("依帳號搜尋", key=_SEARCH, placeholder="（全部）", max_chars=_SEARCH_MAX_LENGTH)
            # Stays clickable while waiting: a newer search replaces the older one.
            st.form_submit_button(
                "搜尋中…" if state.pending and state.page else "搜尋",
                key="admin_users_search_submit",
                type="primary",
                on_click=_request_search,
            )


def _format_time(value: str | None) -> str:
    return format_timestamp(value) if value else _NO_LOGIN


def _frame(items: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [
        {
            "帳號": item["username"],
            "角色": item["role"],
            "註冊時間": _format_time(item["created_at"]),
            "最後登入時間": _format_time(item["last_login_at"]),
        }
        for item in items
    ]
    return pd.DataFrame(rows, columns=list(_COLUMNS))


def _draw_static_table(state: _ListState) -> None:
    assert state.page is not None
    st.dataframe(_frame(state.page["items"]), hide_index=True, column_config=_COLUMNS, placeholder="")


def _draw_table(state: _ListState) -> dict[str, Any] | None:
    """Returns the selected user. Column selection is on so that header clicks cannot sort this page."""
    assert state.page is not None
    items = state.page["items"]
    default = None
    if state.selected_id is not None:
        rows = [index for index, item in enumerate(items) if item["id"] == state.selected_id]
        default = {"selection": {"rows": rows}} if rows else None
    event = st.dataframe(
        _frame(items),
        key=f"admin_users_table_{state.generation}",
        hide_index=True,
        column_config=_COLUMNS,
        placeholder="",
        on_select="rerun",
        selection_mode=["single-row", "single-column"],
        selection_default=default,
        alt="使用者表格：帳號、角色、註冊時間、最後登入時間",
    )
    rows = event.selection.rows
    return items[rows[0]] if rows and rows[0] < len(items) else None


def _draw_pager(state: _ListState, *, waiting: bool) -> None:
    assert state.page is not None
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        st.button(
            "上一頁", key="admin_users_previous", disabled=waiting or len(state.stack) <= 1, on_click=_request_previous
        )
        st.text(f"第 {len(state.stack)} / {_total_pages(state)} 頁", width="content")
        st.button(
            "下一頁", key="admin_users_next", disabled=waiting or not state.page["next_cursor"], on_click=_request_next
        )


def _draw_no_selection_actions() -> None:
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        st.text("選取一列以變更角色", width="content")
        st.button("變更角色", key="admin_users_change", disabled=True)


def _draw_actions(user: dict[str, Any] | None) -> None:
    if user is None:
        _draw_no_selection_actions()
        return
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        # st.text, not Markdown: the account name is user text.
        if user["is_self"]:
            st.text(f"已選取：{user['username']}（你自己）", width="content")
            st.button("變更角色", key="admin_users_change", disabled=True)
            st.text(_SELF_REASON, width="content")
            change = False
        else:
            st.text(f"已選取：{user['username']}（{user['role']}）", width="content")
            change = st.button("變更角色", key="admin_users_change", type="primary")
    if change:
        _open(user)


def _draw_skeleton() -> None:
    with st.container(border=True):
        for _ in range(_SKELETON_ROWS):
            st.skeleton(height=_SKELETON_ROW_HEIGHT)


def _draw_empty(state: _ListState) -> None:
    with st.container(border=True, horizontal_alignment="center"):
        if state.q:
            st.subheader("沒有符合的使用者", anchor=False, text_alignment="center")
            st.text("試著更換關鍵字。", text_alignment="center")
        else:
            st.subheader("還沒有任何使用者", anchor=False, text_alignment="center")


def _draw_message(slot: Any, state: _ListState) -> None:
    if state.error:
        with slot.container(horizontal=True, vertical_alignment="center"):
            st.error(state.error)
            st.button("重試", key="admin_users_retry", on_click=_request_retry)
    elif state.notice and state.notice_is_success:
        slot.success(escape_markdown(state.notice))
    elif state.notice:
        slot.info(escape_markdown(state.notice))


@st.fragment
def _users_list() -> None:
    state = _state()
    _draw_form(state)
    message_slot = st.empty()
    if state.pending:
        if state.page is None:
            _draw_skeleton()
        else:
            message_slot.info("更新中…顯示的是舊結果。")
            if state.page["items"]:
                _draw_static_table(state)
                _draw_pager(state, waiting=True)
                _draw_no_selection_actions()
        _run_pending()
        _rerun_list()

    _draw_message(message_slot, state)
    if state.page is None:
        return
    if not state.page["items"]:
        _draw_empty(state)
        return
    selected = _draw_table(state)
    st.caption(f"時間以 {display_zone().key} 顯示")
    _draw_pager(state, waiting=False)
    _draw_actions(selected)


st.title("使用者管理", anchor=False)
_users_list()
_draw_open_dialog()
