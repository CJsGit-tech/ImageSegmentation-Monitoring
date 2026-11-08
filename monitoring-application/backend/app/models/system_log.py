"""The system log (PRD-14). Written only by app.syslog; the API reads it and never changes it."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Enum, Index, String
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import USERNAME_COLLATION, USERNAME_MAX_LENGTH

EVENT_TYPE_MAX_LENGTH = 32
EVENT_NAME_MAX_LENGTH = 64
SUMMARY_MAX_LENGTH = 255
REQUEST_ID_MAX_LENGTH = 64


class Severity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class SystemLog(Base):
    __tablename__ = "system_logs"
    __table_args__ = (
        # InnoDB appends the primary key, so each index also serves ORDER BY occurred_at, id.
        Index("ix_system_logs_occurred_at", "occurred_at"),
        Index("ix_system_logs_event_type_occurred_at", "event_type", "occurred_at"),
        Index("ix_system_logs_severity_occurred_at", "severity", "occurred_at"),
        Index("ix_system_logs_username_occurred_at", "username", "occurred_at"),
        {"mariadb_charset": "utf8mb4", "mariadb_collate": USERNAME_COLLATION},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Naive UTC with microseconds: when the event happened, not when it was written.
    occurred_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
    # The group the screen filters on (app.syslog.catalog), and the raw audit event type.
    event_type: Mapped[str] = mapped_column(String(EVENT_TYPE_MAX_LENGTH), nullable=False)
    event_name: Mapped[str] = mapped_column(String(EVENT_NAME_MAX_LENGTH), nullable=False)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="system_log_severity", values_callable=lambda levels: [level.value for level in levels]),
        nullable=False,
    )
    # No foreign key: the log keeps who acted even if the account changes, and writing a log
    # row never takes a lock on users.
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str | None] = mapped_column(String(USERNAME_MAX_LENGTH), nullable=True)
    summary: Mapped[str] = mapped_column(String(SUMMARY_MAX_LENGTH), nullable=False)
    # Ids, counts, field names and settings only; never passwords, tokens or record contents.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(REQUEST_ID_MAX_LENGTH), nullable=True)
