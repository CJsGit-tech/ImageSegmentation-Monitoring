"""HttpOnly login cookie, set and cleared by a same-origin route.

Streamlit cannot set cookies, and st.context.cookies is read-only. The script mints a
one-time ticket, the browser POSTs it here, and the response carries Set-Cookie. The token
stays on the server side, so browser JavaScript never sees it.

Tickets live in this process's memory, so the frontend must run as a single replica.
This module must not import streamlit: it also loads in the server's event loop.
"""

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Literal

from starlette.requests import Request
from starlette.responses import Response

COOKIE_NAME = "md_session"
ROUTE_PATH = "/app-auth/session"
TICKET_TTL_SECONDS = 60
_MAX_TICKET_BYTES = 128

Operation = Literal["set", "clear"]


@dataclass(frozen=True)
class _Ticket:
    operation: Operation
    token: str
    max_age: int
    expires_monotonic: float


_lock = threading.Lock()
_tickets: dict[str, _Ticket] = {}


def mint_ticket(operation: Operation, token: str, max_age: int = 0) -> str:
    ticket_id = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        for key in [key for key, ticket in _tickets.items() if ticket.expires_monotonic <= now]:
            del _tickets[key]
        _tickets[ticket_id] = _Ticket(operation, token, max_age, now + TICKET_TTL_SECONDS)
    return ticket_id


def _take_ticket(ticket_id: str) -> _Ticket | None:
    with _lock:
        ticket = _tickets.pop(ticket_id, None)
    if ticket is None or ticket.expires_monotonic <= time.monotonic():
        return None
    return ticket


def _is_same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    return origin is not None and origin == f"{request.url.scheme}://{request.headers.get('host')}"


async def session_route(request: Request) -> Response:
    headers = {"Cache-Control": "no-store"}
    if not _is_same_origin(request):
        return Response(status_code=403, headers=headers)
    body = await request.body()
    ticket = None if len(body) > _MAX_TICKET_BYTES else _take_ticket(body.decode("ascii", "replace"))
    if ticket is None:
        return Response(status_code=403, headers=headers)

    response = Response(status_code=204, headers=headers)
    secure = request.url.scheme == "https"
    if ticket.operation == "set":
        response.set_cookie(
            COOKIE_NAME,
            ticket.token,
            max_age=ticket.max_age,
            path="/",
            secure=secure,
            httponly=True,
            samesite="strict",
        )
    elif request.cookies.get(COOKIE_NAME) == ticket.token:
        # Only the token being discarded: a stale tab must not end a newer login in this browser.
        response.delete_cookie(COOKIE_NAME, path="/", secure=secure, httponly=True, samesite="strict")
    return response
