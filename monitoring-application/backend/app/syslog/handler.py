"""Logging handlers that turn log records into system log rows.

They only build a row and hand it to the writer, so logging stays as fast as before and the
stdout JSON lines are unchanged. Two sources:

- AuditHandler, on the "app.audit" logger: the PRD-14-R6 events listed in app.syslog.catalog.
- SystemErrorHandler, on the root logger: ERROR and above from any logger, plus WARNING and
  above from the simulator and the live hub, as "system_error". The same message is kept at
  most once per THROTTLE_SECONDS. Exception text and tracebacks are never kept, since database
  errors can quote bound values; only the exception class name is.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.core.logging import request_id_var
from app.models.system_log import REQUEST_ID_MAX_LENGTH, Severity
from app.syslog.catalog import EVENT_GROUPS, SYSTEM_ERROR, summarize

Row = dict[str, Any]
Sink = Callable[[Row], None]

THROTTLE_SECONDS = 60.0
DETAILS_MAX_CHARS = 2000
_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName", "color_message"}
_IDENTITY_FIELDS = {"event_type", "user_id", "username"}
_SECRET_MARKERS = ("password", "token", "secret", "authorization", "cookie")
# Loggers whose warnings are system problems; their extras are counts only.
_WARNING_SOURCES = ("app.simulator", "app.realtime")
_OWN_LOGGERS = ("app.audit", "app.syslog")
_SYSTEM_TEXT_FIELDS = {"method", "path"}


def severity_of(levelno: int) -> Severity:
    if levelno >= logging.ERROR:
        return Severity.ERROR
    if levelno >= logging.WARNING:
        return Severity.WARNING
    return Severity.INFO


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {key: value for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS}


def _json_safe(details: dict[str, Any]) -> dict[str, Any] | None:
    if not details:
        return None
    text = json.dumps(details, ensure_ascii=False, default=str)
    if len(text) > DETAILS_MAX_CHARS:
        return {"truncated": True}
    return json.loads(text)


def _row(record: logging.LogRecord, event_name: str, user_id: Any, username: Any, details: dict[str, Any]) -> Row:
    request_id = request_id_var.get()
    return {
        "occurred_at": datetime.fromtimestamp(record.created, UTC).replace(tzinfo=None),
        "event_type": EVENT_GROUPS[event_name],
        "event_name": event_name,
        "severity": severity_of(record.levelno),
        "user_id": user_id if isinstance(user_id, int) and not isinstance(user_id, bool) else None,
        "username": str(username)[:64] if username else None,
        "summary": summarize(event_name, details),
        "details": _json_safe({key: value for key, value in details.items() if key != "message"}),
        "request_id": request_id[:REQUEST_ID_MAX_LENGTH] if request_id else None,
    }


class AuditHandler(logging.Handler):
    def __init__(self, sink: Sink) -> None:
        super().__init__(logging.DEBUG)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event_name = getattr(record, "event_type", None)
            if event_name not in EVENT_GROUPS or event_name == SYSTEM_ERROR:
                return
            extras = _extras(record)
            details = {k: v for k, v in extras.items() if k not in _IDENTITY_FIELDS and not _is_secret(k)}
            self.sink(_row(record, event_name, extras.get("user_id"), extras.get("username"), details))
        except Exception:
            self.handleError(record)


class SystemErrorHandler(logging.Handler):
    def __init__(self, sink: Sink, *, throttle_seconds: float = THROTTLE_SECONDS) -> None:
        super().__init__(logging.WARNING)
        self.sink = sink
        self.throttle_seconds = throttle_seconds
        self._seen: dict[tuple[str, str], tuple[float, int]] = {}
        self._lock = threading.Lock()

    def _wanted(self, record: logging.LogRecord) -> bool:
        name = record.name
        if any(name == own or name.startswith(own + ".") for own in _OWN_LOGGERS):
            return False
        if record.levelno >= logging.ERROR:
            return True
        return any(name == source or name.startswith(source + ".") for source in _WARNING_SOURCES)

    def _suppressed(self, signature: tuple[str, str]) -> int | None:
        """None while throttled; otherwise how many were left out since the last kept one."""
        now = time.monotonic()
        with self._lock:
            last, suppressed = self._seen.get(signature, (None, 0))
            if last is not None and now - last < self.throttle_seconds:
                self._seen[signature] = (last, suppressed + 1)
                return None
            self._seen[signature] = (now, 0)
            return suppressed

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not self._wanted(record):
                return
            # The template, not the formatted message: arguments may carry values.
            template = str(record.msg)[:200]
            suppressed = self._suppressed((record.name, template))
            if suppressed is None:
                return
            details: dict[str, Any] = {"logger": record.name, "message": template}
            if record.exc_info and record.exc_info[0] is not None:
                details["exception"] = record.exc_info[0].__name__
            for key, value in _extras(record).items():
                if _is_secret(key):
                    continue
                if key in _SYSTEM_TEXT_FIELDS and isinstance(value, str):
                    details[key] = value[:200]
                elif isinstance(value, (int, float, bool)):
                    details[key] = value
            if suppressed:
                details["suppressed_count"] = suppressed
            self.sink(_row(record, SYSTEM_ERROR, None, None, details))
        except Exception:
            self.handleError(record)
