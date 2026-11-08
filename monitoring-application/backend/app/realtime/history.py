"""Custom time range view (PRD-08-R12 to R14): saved records, aggregated when there are many.

Covers the simulated device categories, with records from any source. Not live: one query.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.errors import FieldValidationError
from app.core.time_range import (
    MAX_RANGE_MONTHS,
    RangeTooLongError,
    add_months,
    bucket_index,
    bucket_start,
    ensure_within_cap,
    to_naive_utc,
)
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.realtime.hub import RECENT_ANOMALY_LIMIT, aware
from app.realtime.schemas import (
    Aggregation,
    AnomalyItem,
    Bucket,
    Point,
    RangeCategory,
    RangeParams,
    RangeSummary,
    RangeView,
)
from app.records.anomaly import CategoryRange
from app.simulator.devices import DEVICES

# The cap and bucket grid live in app.core.time_range, shared with data analysis (LB-331).
__all__ = ["MAX_RANGE_MONTHS", "RangeTooLongError", "add_months", "check_range", "range_view"]

MAX_POINTS_PER_CATEGORY = 400
# Coarsest last. Each divides a day, so buckets line up with UTC hours and midnights.
BUCKET_CHOICES = (60, 300, 900, 1800, 3600, 10800, 21600, 43200, 86400)


def check_range(params: RangeParams) -> tuple[datetime, datetime]:
    """Returns the range as naive UTC. Exactly 3 months is allowed (PRD-08-R13)."""
    if params.start >= params.end:
        raise FieldValidationError(
            [{"field": "query.end", "message": "結束時間必須晚於開始時間", "type": "range_reversed"}]
        )
    ensure_within_cap(params.start, params.end)
    return to_naive_utc(params.start), to_naive_utc(params.end)


def choose_bucket(span_seconds: float) -> int:
    for seconds in BUCKET_CHOICES:
        if span_seconds / seconds <= MAX_POINTS_PER_CATEGORY:
            return seconds
    return BUCKET_CHOICES[-1]


def bucket_label(seconds: int | None) -> str:
    if seconds is None:
        return "逐筆"
    if seconds % 86400 == 0:
        return f"每 {seconds // 86400} 天"
    if seconds % 3600 == 0:
        return f"每 {seconds // 3600} 小時"
    return f"每 {seconds // 60} 分鐘"


async def range_view(session: AsyncSession, params: RangeParams) -> RangeView:
    start, end = check_range(params)
    categories = [device.category for device in DEVICES]
    in_range = (Record.category.in_(categories), Record.timestamp >= start, Record.timestamp <= end)

    counts = dict(
        (
            await session.execute(
                select(Record.category, func.count()).where(*in_range).group_by(Record.category)
            )
        ).all()
    )
    points: dict[str, list[Bucket]] = {category: [] for category in categories}
    bucket_seconds: int | None = None
    if max(counts.values(), default=0) <= MAX_POINTS_PER_CATEGORY:
        rows = await session.execute(
            select(Record.category, Record.value, Record.timestamp, Record.is_anomaly)
            .where(*in_range)
            .order_by(Record.timestamp, Record.id)
        )
        for row in rows:
            points[row.category].append(
                Bucket(
                    bucket_start=aware(row.timestamp),
                    avg=row.value,
                    min=row.value,
                    max=row.value,
                    count=1,
                    anomaly_count=int(row.is_anomaly),
                )
            )
    else:
        bucket_seconds = choose_bucket((end - start).total_seconds())
        bucket = bucket_index(Record.timestamp, bucket_seconds).label("bucket")
        rows = await session.execute(
            select(
                Record.category,
                bucket,
                func.avg(Record.value).label("avg"),
                func.min(Record.value).label("min"),
                func.max(Record.value).label("max"),
                func.count().label("count"),
                func.sum(case((Record.is_anomaly.is_(True), 1), else_=0)).label("anomalies"),
            )
            .where(*in_range)
            .group_by(Record.category, bucket)
            .order_by(Record.category, bucket)
        )
        for row in rows:
            points[row.category].append(
                Bucket(
                    bucket_start=aware(bucket_start(row.bucket, bucket_seconds)),
                    avg=float(row.avg),
                    min=row.min,
                    max=row.max,
                    count=row.count,
                    anomaly_count=int(row.anomalies or 0),
                )
            )

    latest: dict[str, Point | None] = {}
    for category in categories:
        row = (
            await session.execute(
                select(Record.value, Record.timestamp, Record.is_anomaly)
                .where(Record.category == category, Record.timestamp >= start, Record.timestamp <= end)
                .order_by(Record.timestamp.desc(), Record.id.desc())
                .limit(1)
            )
        ).first()
        latest[category] = (
            None if row is None else Point(value=row.value, timestamp=aware(row.timestamp), is_anomaly=row.is_anomaly)
        )

    settings: dict[str, Any] = {
        row.category: row
        for row in await session.execute(
            select(CategorySetting.category, CategorySetting.range_min, CategorySetting.range_max, CategorySetting.unit)
            .where(CategorySetting.category.in_(categories))
        )
    }

    def range_of(category: str) -> CategoryRange | None:
        setting = settings.get(category)
        if setting is None or setting.range_min is None:
            return None
        return CategoryRange(min=setting.range_min, max=setting.range_max)

    def unit_of(category: str) -> str | None:
        setting = settings.get(category)
        return setting.unit if setting is not None else None

    anomalies = await session.execute(
        select(Record.category, Record.title, Record.value, Record.timestamp)
        .where(*in_range, Record.is_anomaly.is_(True))
        .order_by(Record.timestamp.desc(), Record.id.desc())
        .limit(RECENT_ANOMALY_LIMIT)
    )
    recent = [
        AnomalyItem(
            category=row.category,
            title=row.title,
            value=row.value,
            unit=unit_of(row.category),
            range=range_of(row.category),
            timestamp=aware(row.timestamp),
        )
        for row in anomalies
    ]
    await session.commit()

    result_categories = []
    for device in DEVICES:
        anomaly_count = sum(point.anomaly_count for point in points[device.category])
        category_range = range_of(device.category)
        result_categories.append(
            RangeCategory(
                category=device.category,
                title=device.title,
                unit=unit_of(device.category),
                range=category_range,
                range_status="unset" if category_range is None else "set",
                latest=latest[device.category],
                anomaly_count=anomaly_count,
                points=points[device.category],
            )
        )
    last_times = [point.timestamp for point in latest.values() if point is not None]
    return RangeView(
        start=aware(start),
        end=aware(end),
        aggregation=Aggregation(bucket_seconds=bucket_seconds, label=bucket_label(bucket_seconds)),
        summary=RangeSummary(
            anomalous_category_count=sum(1 for category in result_categories if category.anomaly_count > 0),
            category_count=len(result_categories),
            anomaly_count=sum(category.anomaly_count for category in result_categories),
            last_reading_at=max(last_times, default=None),
        ),
        categories=result_categories,
        recent_anomalies=recent,
    )
