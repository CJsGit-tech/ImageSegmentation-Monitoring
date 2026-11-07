"""Backend API client. Every page calls the backend through get/post/put/patch/delete here."""

import os
from typing import Any

import requests
import streamlit as st

DEFAULT_TIMEOUT_SECONDS = 30
SESSION_LOSS_CODES = frozenset({"UNAUTHORIZED", "TOKEN_EXPIRED", "SESSION_SUPERSEDED", "SESSION_LOGGED_OUT"})
TOKEN_STATE_KEY = "auth_token"


class BackendUnavailable(Exception):
    """The backend could not be reached or did not answer in the API's error format."""


class BackendTimeout(BackendUnavailable):
    """The backend did not answer in time. The request may still have taken effect."""


class ApiError(Exception):
    """The backend refused the request. `message` is written for users."""

    def __init__(self, status: int, code: str, message: str, details: Any = None, request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.request_id = request_id

    def field_errors(self) -> dict[str, str]:
        """Validation details as {field name: message}, keeping the first message for each field."""
        errors: dict[str, str] = {}
        if isinstance(self.details, list):
            for item in self.details:
                if isinstance(item, dict) and item.get("field") and item.get("message"):
                    errors.setdefault(str(item["field"]).rsplit(".", 1)[-1], str(item["message"]))
        return errors


def send(
    method: str,
    path: str,
    *,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> Any:
    """Send one request with no session handling. Returns the parsed JSON, or None if empty."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{os.environ['BACKEND_URL'].rstrip('/')}{path}"
    try:
        response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    except requests.Timeout as exc:
        raise BackendTimeout from exc
    except requests.RequestException as exc:
        raise BackendUnavailable from exc

    if response.ok:
        return response.json() if response.content else None
    try:
        error = response.json()["error"]
        raise ApiError(response.status_code, error["code"], error["message"], error.get("details"), error.get("request_id"))
    except (ValueError, KeyError, TypeError) as exc:
        raise BackendUnavailable from exc


def request(method: str, path: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, **kwargs: Any) -> Any:
    """Send an authenticated request. A lost session leaves the page and goes to SCREEN-01."""
    try:
        return send(method, path, token=st.session_state.get(TOKEN_STATE_KEY), timeout=timeout, **kwargs)
    except ApiError as exc:
        if exc.status == 401 and exc.code in SESSION_LOSS_CODES:
            from core import session  # session imports this module for send().

            session.lose_session(exc.code)
        raise


def get(path: str, **kwargs: Any) -> Any:
    return request("GET", path, **kwargs)


def post(path: str, **kwargs: Any) -> Any:
    return request("POST", path, **kwargs)


def put(path: str, **kwargs: Any) -> Any:
    return request("PUT", path, **kwargs)


def patch(path: str, **kwargs: Any) -> Any:
    return request("PATCH", path, **kwargs)


def delete(path: str, **kwargs: Any) -> Any:
    return request("DELETE", path, **kwargs)
