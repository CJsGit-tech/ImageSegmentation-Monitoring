"""Audit events for PRD-14-R6. Never pass passwords, tokens or record contents here.

Each event is a JSON line on stdout. While the backend runs, app.syslog also keeps the events
listed in app/syslog/catalog.py in the system_logs table, from these same log records.
"""

import logging
from typing import Any

from app.models.user import User

audit_logger = logging.getLogger("app.audit")


def audit(event_type: str, message: str, *, user: User | None = None, level: int = logging.INFO, **fields: Any) -> None:
    audit_logger.log(
        level,
        message,
        extra={
            "event_type": event_type,
            "user_id": user.id if user else None,
            "username": user.username if user else None,
            **fields,
        },
    )
