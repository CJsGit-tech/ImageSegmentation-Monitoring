"""The 3-month query cap (PRD-10-R8, shared by PRD-08-R13 and M4) and time-bucket arithmetic.

The real-time custom range (LB-330) and data analysis (LB-331) both use these, so that the cap
and the bucket grid are one rule. Each caller keeps its own rule for start versus end.
"""

import calendar
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func

from app.core.errors import AppError

MAX_RANGE_MONTHS = 3
# MariaDB TO_SECONDS('1970-01-01 00:00:00'): converts its day count back to Unix time.
TO_SECONDS_AT_UNIX_EPOCH = 62_167_219_200
_UNIX_EPOCH = datetime(1970, 1, 1)


class RangeTooLongError(AppError):
    status_code = 422
    code = "RANGE_TOO_LONG"
    message = "時間範圍超過 3 個月上限。請縮短範圍，或分批查詢。"


def add_months(value: datetime, months: int) -> datetime:
    """Same wall-clock time `months` later, in the value's own offset; the day is clamped to the
    month's last day (11-30 plus 3 months is 02-28, or 02-29 in a leap year)."""
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def ensure_within_cap(start: datetime, end: datetime) -> None:
    """Exactly 3 calendar months (in start's offset) is allowed; anything later is refused."""
    try:
        latest_end = add_months(start, MAX_RANGE_MONTHS)
    except ValueError:
        return  # Beyond year 9999, which no end can pass.
    if end > latest_end:
        raise RangeTooLongError(
            details={"max_months": MAX_RANGE_MONTHS, "latest_allowed_end": latest_end.isoformat()}
        )


def to_naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def offset_seconds(value: datetime) -> int:
    offset = value.utcoffset()
    return int(offset.total_seconds()) if offset is not None else 0


def bucket_index(column: Any, seconds: int, shift_seconds: int = 0) -> ColumnElement[Any]:
    """The bucket a naive-UTC timestamp column falls in. With shift_seconds set to a UTC offset,
    buckets start on that offset's wall-clock boundaries (local midnight for days)."""
    return func.floor((func.to_seconds(column) + shift_seconds) / seconds)


def bucket_start(index: int, seconds: int, shift_seconds: int = 0) -> datetime:
    """Naive UTC start of the bucket that bucket_index returned."""
    unix_seconds = int(index) * seconds - shift_seconds - TO_SECONDS_AT_UNIX_EPOCH
    return _UNIX_EPOCH + timedelta(seconds=unix_seconds)


def python_bucket_index(value: datetime, seconds: int, shift_seconds: int = 0) -> int:
    """The same grid as bucket_index, for a naive-UTC value computed in Python."""
    unix_seconds = (value - _UNIX_EPOCH) // timedelta(seconds=1)
    return (unix_seconds + TO_SECONDS_AT_UNIX_EPOCH + shift_seconds) // seconds
