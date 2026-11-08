"""JSON-lines logging on stdout. Use logging.getLogger(__name__) and pass fields via extra=."""

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName", "color_message"}
_QUIET_PATHS = ("/api/v1/health", "/api/v1/realtime/window")

logger = logging.getLogger("app.request")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "alembic"):
        named = logging.getLogger(name)
        named.handlers.clear()
        named.propagate = True
    # RequestContextMiddleware writes the request log instead.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False


class RequestContextMiddleware:
    """Assigns the request id, writes the request log and turns unhandled errors into a 500."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        fields = {"method": scope["method"], "path": scope["path"]}
        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception("unhandled exception", extra=fields)
            if response_started:
                raise
            from app.core.errors import internal_error_response

            await internal_error_response()(scope, receive, send_with_id)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            quiet = scope["path"].startswith(_QUIET_PATHS) and status < 400
            logger.log(
                logging.DEBUG if quiet else logging.INFO,
                "request",
                extra={**fields, "status": status, "duration_ms": duration_ms},
            )
            request_id_var.reset(token)
