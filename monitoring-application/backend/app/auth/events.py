"""Authentication and permission events for PRD-14-R6. Never pass passwords or tokens here."""

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
