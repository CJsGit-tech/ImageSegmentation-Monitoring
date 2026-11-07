"""Shared page states and small components (SCREEN-04, SHARED-04, SHARED-05, SHARED-06)."""

from collections.abc import Callable

import streamlit as st

from core import api, nav, session

_AUTO_RETRY_SECONDS = 10


def unreachable_page() -> None:
    """SCREEN-04: replaces the page while the backend cannot be reached."""
    with st.container(horizontal_alignment="center"):
        st.subheader("無法連到系統，稍後會自動重試", anchor=False, text_alignment="center")
        st.button("重試", type="primary", key="screen04_retry")
        _auto_retry()


@st.fragment(run_every=_AUTO_RETRY_SECONDS)
def _auto_retry() -> None:
    if session.backend_is_reachable():
        st.rerun(scope="app")


def no_permission_page(role: str) -> None:
    """SCREEN-04: shown when a URL opens a page this role cannot use."""
    with st.container(horizontal_alignment="center"):
        st.subheader(f"你的角色（{role}）不能使用此功能", anchor=False, text_alignment="center")
        st.page_link(nav.home_page(), label="回到即時監控", width="content")


def not_available_page(title: str, screen_id: str) -> None:
    """Marks a page whose ticket has not been delivered. Each ticket replaces its page file."""
    st.title(title, anchor=False)
    st.info(f"此頁面尚未開放（{screen_id}）。")


def disabled_action(label: str, reason: str, *, key: str) -> None:
    """SHARED-04: a disabled action with its reason as text beside it."""
    with st.container(horizontal=True, vertical_alignment="center"):
        st.button(label, disabled=True, key=key)
        st.caption(reason)


def show_api_error(error: api.ApiError) -> None:
    """SHARED-04: shows the backend's own reason for a refusal."""
    st.error(error.message)


def confirm_dialog(
    title: str,
    *,
    target: str,
    consequence: str,
    confirm_label: str,
    on_confirm: Callable[[], None],
) -> None:
    """SHARED-06: call when the triggering button is pressed. Closing or Esc changes nothing."""

    @st.dialog(title)
    def _dialog() -> None:
        st.write(target)
        st.write(consequence)
        with st.container(horizontal=True):
            if st.button("取消", key="confirm_dialog_cancel"):
                st.rerun()
            if st.button(confirm_label, type="primary", key="confirm_dialog_confirm"):
                on_confirm()
                st.rerun()

    _dialog()
