"""Login state: session_state holds the working copy, the HttpOnly cookie survives refresh."""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import streamlit as st

from core import api, cookie_store, nav

_ME_TIMEOUT_SECONDS = 5
_LOGOUT_TIMEOUT_SECONDS = 3
_COOKIE_GRACE_SECONDS = 86400
_COOKIE_RETRY_SECONDS = 30

_USER = "auth_user"
_NOTICE = "auth_notice"
_INITIALIZED = "auth_initialized"
_COOKIE_OP = "auth_cookie_op"
_LOGOUT_REQUESTED = "auth_logout_requested"
_KEPT_ON_DISCARD = {_INITIALIZED, _NOTICE, _COOKIE_OP}

Notice = Literal["logged_out", "logged_out_local", "expired", "superseded"]
_NOTICE_BY_CODE: dict[str, Notice] = {
    "TOKEN_EXPIRED": "expired",
    "SESSION_SUPERSEDED": "superseded",
    "SESSION_LOGGED_OUT": "logged_out",
}
SyncStatus = Literal["authenticated", "anonymous", "unavailable"]

_cookie_sync = st.components.v2.component(
    "auth_cookie_sync",
    js=f"""
export default function (component) {{
  const {{ data, setTriggerValue, parentElement }} = component;
  if (!data || !data.ticket || parentElement.__ticket === data.ticket) return;
  parentElement.__ticket = data.ticket;
  const report = (status) => setTriggerValue("done", {{ ticket: data.ticket, status }});
  fetch("{cookie_store.ROUTE_PATH}", {{ method: "POST", body: data.ticket, credentials: "same-origin" }})
    .then((response) => report(response.status))
    .catch(() => report(0));
}}
""",
)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    role: str
    capabilities: frozenset[str]


def _to_user(body: dict) -> CurrentUser:
    return CurrentUser(body["id"], body["username"], body["role"], frozenset(body["capabilities"]))


def current_user() -> CurrentUser | None:
    return st.session_state.get(_USER)


def can(capability: str) -> bool:
    user = current_user()
    return user is not None and capability in user.capabilities


def notice() -> Notice | None:
    """Why the user reached SCREEN-01. Stays across reruns, until login or opening SCREEN-02."""
    return st.session_state.get(_NOTICE)


def clear_notice() -> None:
    st.session_state.pop(_NOTICE, None)


def _queue_cookie(operation: cookie_store.Operation, token: str, max_age: int = 0) -> None:
    st.session_state[_COOKIE_OP] = {"operation": operation, "token": token, "max_age": max_age}


def _discard_login(reason: Notice | None) -> None:
    """Drop everything from this login, including unsent form content (SHARED-08)."""
    token = st.session_state.get(api.TOKEN_STATE_KEY)
    for key in [key for key in st.session_state if key not in _KEPT_ON_DISCARD]:
        del st.session_state[key]
    if token:
        _queue_cookie("clear", token)
    if reason:
        st.session_state[_NOTICE] = reason


def sync() -> SyncStatus:
    """Decide the login state for this script run. Asks the backend on every full run."""
    state = st.session_state
    first_run = _INITIALIZED not in state
    if first_run:
        # Read once: st.context.cookies stays frozen at the WebSocket handshake.
        state[_INITIALIZED] = True
        cookie_token = st.context.cookies.get(cookie_store.COOKIE_NAME)
        if cookie_token:
            state[api.TOKEN_STATE_KEY] = cookie_token

    if state.pop(_LOGOUT_REQUESTED, False):
        _logout()

    token = state.get(api.TOKEN_STATE_KEY)
    if not token:
        return "anonymous"
    try:
        if first_run:
            with st.spinner("載入中…"):
                body = api.send("GET", "/api/v1/auth/me", token=token, timeout=_ME_TIMEOUT_SECONDS)
        else:
            body = api.send("GET", "/api/v1/auth/me", token=token, timeout=_ME_TIMEOUT_SECONDS)
    except api.BackendUnavailable:
        return "unavailable"
    except api.ApiError as exc:
        if exc.status >= 500:
            return "unavailable"
        if exc.status == 401:
            _discard_login(_NOTICE_BY_CODE.get(exc.code))
            return "anonymous"
        raise
    state[_USER] = _to_user(body)
    return "authenticated"


def backend_is_reachable() -> bool:
    try:
        api.send("GET", "/api/v1/health/ready", timeout=_ME_TIMEOUT_SECONDS)
    except (api.BackendUnavailable, api.ApiError):
        return False
    return True


def login(username: str, password: str) -> CurrentUser:
    """Log in. Raises ApiError or BackendUnavailable; the caller decides where to go next."""
    body = api.send("POST", "/api/v1/auth/login", json={"username": username, "password": password})
    token = body["access_token"]
    expires_at = datetime.fromisoformat(body["expires_at"]).timestamp()
    # Outlives the token, so an expired token still reaches the backend and earns the expired notice.
    max_age = max(0, int(expires_at - time.time())) + _COOKIE_GRACE_SECONDS
    st.session_state[api.TOKEN_STATE_KEY] = token
    user = _to_user(body["user"])
    st.session_state[_USER] = user
    _queue_cookie("set", token, max_age)
    clear_notice()
    return user


def request_logout() -> None:
    """on_click for the logout button. Runs before the next login check, so it works with the backend down."""
    st.session_state[_LOGOUT_REQUESTED] = True


def _logout() -> None:
    """End this login. Local state clears even if the backend does not answer."""
    reason: Notice = "logged_out"
    try:
        api.send(
            "POST",
            "/api/v1/auth/logout",
            token=st.session_state.get(api.TOKEN_STATE_KEY),
            timeout=_LOGOUT_TIMEOUT_SECONDS,
        )
    except api.BackendUnavailable:
        reason = "logged_out_local"
    except api.ApiError as exc:
        # A 401 means this login has already ended, which is the outcome logout wants.
        if exc.status != 401:
            reason = "logged_out_local"
    _discard_login(reason)


def lose_session(code: str) -> None:
    """A request was answered with a lost session: go to SCREEN-01 with the matching notice."""
    _discard_login(_NOTICE_BY_CODE.get(code))
    st.switch_page(nav.login_page())


def render_cookie_sync() -> None:
    """Carry out a queued cookie change through the browser. Renders nothing visible."""
    op = st.session_state.get(_COOKIE_OP)
    if op is None or op.get("retry_after", 0) > time.time():
        return
    if "ticket" not in op or time.time() - op["minted_at"] >= cookie_store.TICKET_TTL_SECONDS - 10:
        op["ticket"] = cookie_store.mint_ticket(op["operation"], op["token"], op["max_age"])
        op["minted_at"] = time.time()
    result = _cookie_sync(data={"ticket": op["ticket"]}, key="auth_cookie_sync", on_done_change=lambda: None)
    done = result.done if result else None
    if done and done.get("ticket") == op["ticket"]:
        if done.get("status") == 204:
            del st.session_state[_COOKIE_OP]
        else:
            del op["ticket"]
            op["retry_after"] = time.time() + _COOKIE_RETRY_SECONDS
