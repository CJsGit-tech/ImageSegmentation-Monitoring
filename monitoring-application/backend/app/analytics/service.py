"""Analysis queries. Every function takes the same AnalysisQuery, so the screen and the Excel
export (LB-332) see the same records: check_query, then the query you need.

Rules (working interpretations confirmed by the TPM, each kept in one place here):
- Both edges are inclusive (PRD-10-R2): see record_conditions.
- start may equal end; only start later than end is refused (PRD-10-R7): see check_query.
- The 3-month cap is the one shared with the real-time range: app.core.time_range.
- No category given lists only categories with data; given ones each get a row: _row_categories.
- count is returned for manual checking and is not displayed (PRD-10-R10, R12).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.analytics.schemas import (
    GRANULARITY_LABELS,
    GRANULARITY_SECONDS,
    AnalysisQuery,
    AnomalyListQuery,
    CategoryStatistics,
    StatisticsResult,
    TrendPoint,
    TrendQuery,
    TrendResult,
    TrendSeries,
)
from app.auth.errors import FieldValidationError
from app.core.errors import AppError
from app.core.time_range import (
    bucket_index,
    bucket_start,
    ensure_within_cap,
    offset_seconds,
    python_bucket_index,
    to_naive_utc,
)
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.records.anomaly import CategoryRange
from app.records.pagination import decode_cursor, encode_cursor, order_by_clause, seek_condition
from app.records.schemas import RecordOut, RecordPage

MAX_POINTS_PER_CATEGORY = 2500
DETAIL_BATCH_SIZE = 5000


class TooManyPointsError(AppError):
    status_code = 422
    code = "TOO_MANY_POINTS"
    message = "所選粒度在此時間範圍會產生過多資料點，請改選較粗的粒度。"


@dataclass(frozen=True)
class Window:
    """A checked query: naive-UTC bounds for SQL plus what responses echo back."""

    start: datetime  # As requested, with its offset.
    end: datetime
    start_utc: datetime
    end_utc: datetime
    categories: list[str] | None  # None means every category.


def check_query(query: AnalysisQuery) -> Window:
    """Raises the same errors the HTTP endpoints return. Call it before any other function."""
    if query.start > query.end:
        raise FieldValidationError(
            [{"field": "query.end", "message": "結束時間不可早於開始時間", "type": "range_reversed"}]
        )
    ensure_within_cap(query.start, query.end)
    categories = list(dict.fromkeys(query.category)) if query.category else None
    return Window(query.start, query.end, to_naive_utc(query.start), to_naive_utc(query.end), categories)


def record_conditions(window: Window) -> list[ColumnElement[bool]]:
    """The records the analysis covers, from every source (PRD-10-R4). Both edges included."""
    conditions: list[ColumnElement[bool]] = [Record.timestamp >= window.start_utc, Record.timestamp <= window.end_utc]
    if window.categories is not None:
        conditions.append(Record.category.in_(window.categories))
    return conditions


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def _row_categories(window: Window, found: list[str]) -> list[str]:
    return window.categories if window.categories is not None else sorted(found)


async def _settings(session: AsyncSession, categories: list[str]) -> dict[str, Any]:
    if not categories:
        return {}
    rows = await session.execute(
        select(CategorySetting.category, CategorySetting.range_min, CategorySetting.range_max, CategorySetting.unit)
        .where(CategorySetting.category.in_(categories))
    )
    return {row.category: row for row in rows}


def _range_of(setting: Any) -> CategoryRange | None:
    if setting is None or setting.range_min is None:
        return None
    return CategoryRange(min=setting.range_min, max=setting.range_max)


# --- Statistics (PRD-10) ---


def statistics_statement(window: Window) -> Select[Any]:
    return (
        select(
            Record.category,
            func.count().label("count"),
            func.sum(Record.value).label("sum"),
            func.avg(Record.value).label("avg"),
            func.max(Record.value).label("max"),
            func.min(Record.value).label("min"),
            func.sum(case((Record.is_anomaly.is_(True), 1), else_=0)).label("anomalies"),
        )
        .where(*record_conditions(window))
        .group_by(Record.category)
    )


async def category_statistics(session: AsyncSession, query: AnalysisQuery) -> StatisticsResult:
    window = check_query(query)
    rows = await session.execute(statistics_statement(window))
    found = {row.category: row for row in rows}
    names = _row_categories(window, list(found))
    settings = await _settings(session, names)
    items = []
    for name in names:
        row = found.get(name)
        setting = settings.get(name)
        unit = setting.unit if setting is not None else None
        if row is None:
            items.append(
                CategoryStatistics(
                    category=name, unit=unit, count=0, anomaly_count=0, sum=None, avg=None, max=None, min=None
                )
            )
        else:
            items.append(
                CategoryStatistics(
                    category=name,
                    unit=unit,
                    count=row.count,
                    anomaly_count=int(row.anomalies),
                    sum=float(row.sum),
                    avg=float(row.avg),
                    max=row.max,
                    min=row.min,
                )
            )
    return StatisticsResult(
        start=_aware(window.start_utc),
        end=_aware(window.end_utc),
        category=window.categories,
        has_data=bool(found),
        categories=items,
    )


# --- Trend (PRD-11) ---


def points_per_category(window: Window, bucket_seconds: int) -> int:
    """Buckets the range touches on the grid, whatever the data: the check runs before querying."""
    shift = offset_seconds(window.start)
    return (
        python_bucket_index(window.end_utc, bucket_seconds, shift)
        - python_bucket_index(window.start_utc, bucket_seconds, shift)
        + 1
    )


def check_granularity(window: Window, granularity: str) -> int:
    count = points_per_category(window, GRANULARITY_SECONDS[granularity])
    if count > MAX_POINTS_PER_CATEGORY:
        coarser = [
            name
            for name, seconds in GRANULARITY_SECONDS.items()
            if seconds > GRANULARITY_SECONDS[granularity] and points_per_category(window, seconds) <= MAX_POINTS_PER_CATEGORY
        ]
        raise TooManyPointsError(
            details={
                "granularity": granularity,
                "points_per_category": count,
                "max_points": MAX_POINTS_PER_CATEGORY,
                "coarser": coarser,
            }
        )
    return count


def _offset_text(shift: int) -> str:
    sign = "-" if shift < 0 else "+"
    hours, minutes = divmod(abs(shift) // 60, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def trend_statement(window: Window, granularity: str) -> Select[Any]:
    seconds = GRANULARITY_SECONDS[granularity]
    bucket = bucket_index(Record.timestamp, seconds, offset_seconds(window.start)).label("bucket")
    anomalous_value = case((Record.is_anomaly.is_(True), Record.value))
    return (
        select(
            Record.category,
            bucket,
            func.avg(Record.value).label("avg"),
            func.min(Record.value).label("min"),
            func.max(Record.value).label("max"),
            func.count().label("count"),
            func.sum(case((Record.is_anomaly.is_(True), 1), else_=0)).label("anomalies"),
            func.min(anomalous_value).label("anomaly_min"),
            func.max(anomalous_value).label("anomaly_max"),
        )
        .where(*record_conditions(window))
        .group_by(Record.category, bucket)
        .order_by(Record.category, bucket)
    )


async def trend(session: AsyncSession, query: TrendQuery) -> TrendResult:
    window = check_query(query)
    check_granularity(window, query.granularity)
    seconds = GRANULARITY_SECONDS[query.granularity]
    shift = offset_seconds(window.start)
    zone = timezone(timedelta(seconds=shift))
    rows = await session.execute(trend_statement(window, query.granularity))
    points: dict[str, list[TrendPoint]] = {}
    for row in rows:
        points.setdefault(row.category, []).append(
            TrendPoint(
                bucket_start=bucket_start(row.bucket, seconds, shift).replace(tzinfo=UTC).astimezone(zone),
                avg=float(row.avg),
                min=row.min,
                max=row.max,
                count=row.count,
                anomaly_count=int(row.anomalies or 0),
                anomaly_min=row.anomaly_min,
                anomaly_max=row.anomaly_max,
            )
        )
    names = _row_categories(window, list(points))
    settings = await _settings(session, names)
    series = []
    for name in names:
        category_range = _range_of(settings.get(name))
        setting = settings.get(name)
        series.append(
            TrendSeries(
                category=name,
                unit=setting.unit if setting is not None else None,
                range=category_range,
                range_status="unset" if category_range is None else "set",
                points=points.get(name, []),
            )
        )
    return TrendResult(
        start=_aware(window.start_utc),
        end=_aware(window.end_utc),
        category=window.categories,
        granularity=query.granularity,
        bucket_seconds=seconds,
        label=GRANULARITY_LABELS[query.granularity],
        utc_offset=_offset_text(shift),
        max_points_per_category=MAX_POINTS_PER_CATEGORY,
        has_data=bool(points),
        categories=series,
    )


# --- Anomaly list (PRD-10-R11) ---


def anomaly_conditions(window: Window) -> list[ColumnElement[bool]]:
    # "= true", not "IS TRUE": only an equality lets MariaDB seek ix_records_is_anomaly_timestamp.
    return [*record_conditions(window), Record.is_anomaly == true()]


def _record_out(record: Record) -> RecordOut:
    return RecordOut.from_record(record, record.creator.username if record.creator else None)


async def anomaly_page(session: AsyncSession, query: AnomalyListQuery) -> RecordPage:
    """Same item shape as the records list, keyset-paged the same way (LB-327)."""
    window = check_query(query)
    conditions = anomaly_conditions(window)
    page_conditions = list(conditions)
    if query.cursor is not None:
        key, record_id = decode_cursor(query.cursor, query.sort_by, query.order)
        page_conditions.append(seek_condition(query.sort_by, query.order, key, record_id))
    total = await session.scalar(select(func.count()).select_from(Record).where(*conditions))
    rows = list(
        await session.scalars(
            select(Record)
            .options(joinedload(Record.creator))
            .where(*page_conditions)
            .order_by(*order_by_clause(query.sort_by, query.order))
            .limit(query.limit + 1)
        )
    )
    page = rows[: query.limit]
    return RecordPage(
        items=[_record_out(row) for row in page],
        total=total or 0,
        limit=query.limit,
        next_cursor=encode_cursor(query.sort_by, query.order, page[-1]) if len(rows) > query.limit else None,
    )


# --- For the Excel export (LB-332) ---


async def iter_records(
    session: AsyncSession, query: AnalysisQuery, batch_size: int = DETAIL_BATCH_SIZE
) -> AsyncIterator[RecordOut]:
    """Every record the statistics counted, oldest first (timestamp, then id), in keyset batches,
    so memory stays flat for large ranges. The number yielded equals the sum of the counts."""
    window = check_query(query)
    conditions = record_conditions(window)
    last: Record | None = None
    while True:
        seek = [] if last is None else [seek_condition("timestamp", "asc", last.timestamp, last.id)]
        batch = list(
            await session.scalars(
                select(Record)
                .options(joinedload(Record.creator))
                .where(*conditions, *seek)
                .order_by(*order_by_clause("timestamp", "asc"))
                .limit(batch_size)
            )
        )
        for record in batch:
            yield _record_out(record)
        if len(batch) < batch_size:
            return
        last = batch[-1]
