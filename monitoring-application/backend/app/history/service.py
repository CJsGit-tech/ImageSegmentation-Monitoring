"""The system-record history query (PRD-16).

Rules, each kept in one place:
- Only system-generated records: created_by_user_id IS NULL, written only by the simulator
  (PRD-07-R10, PRD-16-R6). Manual and imported records always carry a user id.
- Records edited by an Admin stay system records and show their latest content; records keep
  no earlier versions (PRD-05-R9). is_anomaly is the stored judgement.
- Range checks (start <= end, 3-month cap, both edges inclusive) are the analysis ones.
"""

from typing import Any

from sqlalchemy import ColumnElement, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import check_query, record_conditions
from app.history.schemas import HISTORY_SORT, HistoryQuery
from app.models.record import Record
from app.records.pagination import decode_cursor, encode_cursor, order_by_clause, seek_condition
from app.records.schemas import RecordOut, RecordPage


def history_conditions(query: HistoryQuery) -> list[ColumnElement[bool]]:
    """Raises the HTTP endpoint's validation errors."""
    window = check_query(query)
    conditions: list[ColumnElement[Any]] = [*record_conditions(window), Record.created_by_user_id.is_(None)]
    if query.anomaly_only:
        # "= true", not "IS TRUE": only an equality lets MariaDB seek ix_records_is_anomaly_timestamp.
        conditions.append(Record.is_anomaly == true())
    return conditions


async def history_page(session: AsyncSession, query: HistoryQuery) -> RecordPage:
    conditions = history_conditions(query)
    page_conditions = list(conditions)
    if query.cursor is not None:
        key, record_id = decode_cursor(query.cursor, HISTORY_SORT, query.order)
        page_conditions.append(seek_condition(HISTORY_SORT, query.order, key, record_id))
    total = await session.scalar(select(func.count()).select_from(Record).where(*conditions))
    # One extra row tells whether another page exists.
    rows = list(
        await session.scalars(
            # No creator join: every row here is a system record.
            select(Record)
            .where(*page_conditions)
            .order_by(*order_by_clause(HISTORY_SORT, query.order))
            .limit(query.limit + 1)
        )
    )
    page = rows[: query.limit]
    return RecordPage(
        items=[RecordOut.from_record(row, None) for row in page],
        total=total or 0,
        limit=query.limit,
        next_cursor=encode_cursor(HISTORY_SORT, query.order, page[-1]) if len(rows) > query.limit else None,
    )
