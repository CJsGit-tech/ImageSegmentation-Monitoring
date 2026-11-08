"""Reading the system log: filters, newest first, keyset paging (PRD-14-R4, R8, R9). Read only."""

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.errors import FieldValidationError
from app.core.time_range import MAX_RANGE_MONTHS, ensure_within_cap, to_naive_utc
from app.models.system_log import Severity, SystemLog
from app.syslog.catalog import EVENT_TYPE_LABELS, SEVERITY_LABELS
from app.syslog.schemas import LogItem, LogOptions, LogPage, LogQuery, Option


def _invalid_cursor(reason: str) -> FieldValidationError:
    return FieldValidationError([{"field": "query.cursor", "message": reason, "type": "cursor_invalid"}])


def _fingerprint(params: LogQuery, start: datetime, end: datetime, username: str | None) -> str:
    conditions = {
        "s": start.isoformat(),
        "e": end.isoformat(),
        "t": sorted(set(params.event_type or [])),
        "v": sorted({level.value for level in params.severity or []}),
        "u": username.casefold() if username else None,
    }
    raw = json.dumps(conditions, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _encode_cursor(fingerprint: str, row: SystemLog) -> str:
    raw = json.dumps({"f": fingerprint, "t": row.occurred_at.isoformat(), "i": row.id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, fingerprint: str) -> tuple[datetime, int]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        cursor_fingerprint, occurred_at, row_id = payload["f"], datetime.fromisoformat(payload["t"]), payload["i"]
    except (ValueError, TypeError, KeyError, binascii.Error, OverflowError):
        raise _invalid_cursor("cursor 無效，請從第一頁重新查詢") from None
    if occurred_at.tzinfo is not None or isinstance(row_id, bool) or not isinstance(row_id, int):
        raise _invalid_cursor("cursor 無效，請從第一頁重新查詢")
    if cursor_fingerprint != fingerprint:
        raise _invalid_cursor("cursor 與目前的查詢條件不一致，請從第一頁重新查詢")
    return occurred_at, row_id


def check_range(params: LogQuery) -> tuple[datetime, datetime]:
    """Both ends included. Exactly 3 months is allowed; one second more is refused (R9)."""
    if params.start >= params.end:
        raise FieldValidationError(
            [{"field": "query.end", "message": "結束時間必須晚於開始時間", "type": "range_reversed"}]
        )
    ensure_within_cap(params.start, params.end)
    return to_naive_utc(params.start), to_naive_utc(params.end)


def _item(row: SystemLog) -> LogItem:
    return LogItem(
        id=row.id,
        occurred_at=row.occurred_at.replace(tzinfo=UTC),
        event_type=row.event_type,
        event_label=EVENT_TYPE_LABELS.get(row.event_type, row.event_type),
        event_name=row.event_name,
        severity=row.severity,
        severity_label=SEVERITY_LABELS[row.severity],
        user_id=row.user_id,
        username=row.username,
        summary=row.summary,
        details=row.details,
    )


async def list_logs(session: AsyncSession, params: LogQuery) -> LogPage:
    start, end = check_range(params)
    username = params.username or None
    conditions: list[ColumnElement[bool]] = [SystemLog.occurred_at >= start, SystemLog.occurred_at <= end]
    if params.event_type:
        conditions.append(SystemLog.event_type.in_(sorted(set(params.event_type))))
    if params.severity:
        conditions.append(SystemLog.severity.in_(sorted(set(params.severity))))
    if username:
        conditions.append(SystemLog.username == username)

    fingerprint = _fingerprint(params, start, end, username)
    page_conditions = list(conditions)
    if params.cursor is not None:
        occurred_at, row_id = _decode_cursor(params.cursor, fingerprint)
        page_conditions.append(
            or_(SystemLog.occurred_at < occurred_at, and_(SystemLog.occurred_at == occurred_at, SystemLog.id < row_id))
        )

    total = await session.scalar(select(func.count()).select_from(SystemLog).where(*conditions))
    rows = list(
        await session.scalars(
            select(SystemLog)
            .where(*page_conditions)
            .order_by(SystemLog.occurred_at.desc(), SystemLog.id.desc())
            .limit(params.limit + 1)
        )
    )
    page = rows[: params.limit]
    has_next = len(rows) > params.limit
    return LogPage(
        items=[_item(row) for row in page],
        total=total or 0,
        limit=params.limit,
        next_cursor=_encode_cursor(fingerprint, page[-1]) if has_next else None,
    )


def options(retention_days: float) -> LogOptions:
    return LogOptions(
        event_types=[Option(value=key, label=label) for key, label in EVENT_TYPE_LABELS.items()],
        severities=[Option(value=level.value, label=SEVERITY_LABELS[level]) for level in Severity],
        retention_days=retention_days,
        max_range_months=MAX_RANGE_MONTHS,
    )
