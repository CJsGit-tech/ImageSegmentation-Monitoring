"""Application shell: decides the login state, builds navigation and guards every page."""

import streamlit as st

from core import forms, nav, session, ui

_LAST_PAGE = "shell_last_page"

status = session.sync()
user = session.current_user() if status == "authenticated" else None
capabilities = user.capabilities if user else frozenset()

st.set_page_config(
    layout="wide" if user else "centered",
    initial_sidebar_state="auto" if user else "collapsed",
)

# Always "sidebar": anonymous users have only hidden pages, so nothing shows, whereas
# "hidden" leaves an empty sidebar mounted after logout until the next rerun finishes.
page = st.navigation(
    nav.navigation_pages(capabilities),
    position="sidebar",
    expanded=True,
)
session.render_cookie_sync()

if st.session_state.get(_LAST_PAGE) != page.url_path:
    forms.reset_interrupted_submits()
    if nav.is_register(page):
        session.clear_notice()
    st.session_state[_LAST_PAGE] = page.url_path

if status == "unavailable":
    ui.unreachable_page()
elif user is None:
    if not nav.is_public(page):
        st.switch_page(nav.login_page())
    page.run()
else:
    if nav.is_public(page):
        st.switch_page(nav.home_page())
    st.logo("assets/wordmark.svg", size="large")
    with st.sidebar:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.text(user.username)
            st.badge(user.role, color="gray")
        st.button("登出", key="shell_logout", on_click=session.request_logout)
    if session.can(nav.required_capability(page)):
        page.run()
    else:
        ui.no_permission_page(user.role)
