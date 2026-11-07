"""Keyset pagination: a page starts after a position in the sort order, not at a row count.

Rows inserted or deleted while someone pages cannot shift a row across a page boundary, which
OFFSET cannot guarantee while the simulator is writing. The id breaks ties under every sort.
"""

import base64
import binascii
import json
import math
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import InstrumentedAttribute

from app.auth.errors import FieldValidationError
from app.models.record import Record

_SORT_COLUMNS: dict[str, InstrumentedAttribute[Any]] = {
    "title": Record.title,
    "value": Record.value,
    "category": Record.category,
    "timestamp": Record.timestamp,
}


def _invalid_cursor(reason: str) -> FieldValidationError:
    return FieldValidationError([{"field": "query.cursor", "message": reason, "type": "cursor_invalid"}])


def _key_to_json(sort_by: str, key: Any) -> Any:
    # json writes floats so that they read back exactly, -0.0 included.
    return key.isoformat() if sort_by == "timestamp" else key


def _key_from_json(sort_by: str, key: Any) -> Any:
    if sort_by == "timestamp":
        parsed = datetime.fromisoformat(key)
        if parsed.tzinfo is not None:
            raise ValueError
        return parsed
    if sort_by == "value":
        if isinstance(key, bool) or not isinstance(key, (int, float)) or not math.isfinite(key):
            raise ValueError
        return float(key)
    if not isinstance(key, str):
        raise ValueError
    return key


def encode_cursor(sort_by: str, order: str, record: Record) -> str:
    payload = {"s": sort_by, "o": order, "k": _key_to_json(sort_by, getattr(record, sort_by)), "i": record.id}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, sort_by: str, order: str) -> tuple[Any, int]:
    """Returns the key and id to continue after. Refuses a cursor from another sort."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        cursor_sort, cursor_order = payload["s"], payload["o"]
        key = _key_from_json(sort_by, payload["k"]) if cursor_sort == sort_by else None
        record_id = payload["i"]
    except (ValueError, TypeError, KeyError, binascii.Error, OverflowError):
        raise _invalid_cursor("cursor 無效，請從第一頁重新查詢") from None
    if isinstance(record_id, bool) or not isinstance(record_id, int):
        raise _invalid_cursor("cursor 無效，請從第一頁重新查詢")
    if cursor_sort != sort_by or cursor_order != order:
        raise _invalid_cursor("cursor 與目前的排序不一致，請從第一頁重新查詢")
    return key, record_id


def order_by_clause(sort_by: str, order: str) -> tuple[ColumnElement[Any], ColumnElement[Any]]:
    # The id follows the sort direction, so that each single-column index can serve the sort.
    column = _SORT_COLUMNS[sort_by]
    if order == "asc":
        return column.asc(), Record.id.asc()
    return column.desc(), Record.id.desc()


def seek_condition(sort_by: str, order: str, key: Any, record_id: int) -> ColumnElement[bool]:
    # Compared by the database, so title and category ties follow the column collation,
    # the same rule ORDER BY uses.
    column = _SORT_COLUMNS[sort_by]
    if order == "asc":
        return or_(column > key, and_(column == key, Record.id > record_id))
    return or_(column < key, and_(column == key, Record.id < record_id))
