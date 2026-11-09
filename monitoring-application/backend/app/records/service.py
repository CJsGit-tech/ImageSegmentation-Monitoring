"""Record creation, listing, updates and deletion, including the ownership rule (PRD-05)."""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.auth.errors import FieldValidationError
from app.auth.events import audit
from app.auth.permissions import Capability, user_can
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.user import User
from app.records.anomaly import get_range, is_out_of_range, judge_manual_save
from app.records.errors import ClientRequestReusedError, NotRecordOwnerError, RecordNotFoundError
from app.records.pagination import decode_cursor, encode_cursor, order_by_clause, seek_condition
from app.records.schemas import (
    AnomalyCheck,
    AnomalyCheckParams,
    RecordCreate,
    RecordListParams,
    RecordOut,
    RecordPage,
    RecordUpdate,
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def build_record(
    *,
    title: str,
    value: float,
    category: str,
    timestamp: datetime | None,
    creator: User | None,
    is_anomaly: bool = False,
    client_request_id: str | None = None,
) -> Record:
    """Shared by every way of adding records. A creator of None marks a system record.

    Bulk writers call this and then add their own transaction and audit handling. The
    timestamp must already be naive UTC. Commit any earlier reads before inserting: see
    _end_read_snapshot.
    """
    return Record(
        title=title,
        value=value,
        category=category,
        timestamp=timestamp or _utc_now(),
        created_by_user_id=creator.id if creator else None,
        is_anomaly=is_anomaly,
        client_request_id=client_request_id,
    )


async def _end_read_snapshot(session: AsyncSession) -> None:
    """Authentication and other reads leave a snapshot open on the session.

    With a snapshot open, MariaDB's innodb_snapshot_isolation fails a locking read of a row
    changed since (error 1020). That covers SELECT FOR UPDATE and the foreign key check of an
    INSERT, whose users row changes on every login. Nothing is lost: nothing was written yet.
    """
    await session.commit()


async def _already_created(session: AsyncSession, user_id: int, payload: RecordCreate) -> Record | None:
    """The record an earlier send of this same request wrote, if any."""
    if payload.client_request_id is None:
        return None
    record = await session.scalar(
        select(Record).where(
            Record.client_request_id == str(payload.client_request_id),
            Record.created_by_user_id == user_id,
        )
    )
    if record is None:
        return None
    same_content = (record.title, record.value, record.category) == (payload.title, payload.value, payload.category)
    if not same_content or (payload.timestamp is not None and record.timestamp != payload.timestamp):
        raise ClientRequestReusedError(details={"record_id": record.id})
    return record


async def create_record(session: AsyncSession, user: User, payload: RecordCreate) -> RecordOut:
    # A rollback below expires these objects, and an async session cannot reload them on access.
    user_id, username = user.id, user.username
    # Before judging: a retry of a saved record must get that record, whatever the range is now.
    if (existing := await _already_created(session, user_id, payload)) is not None:
        return RecordOut.from_record(existing, username)

    record = build_record(
        title=payload.title,
        value=payload.value,
        category=payload.category,
        timestamp=payload.timestamp,
        creator=user,
        is_anomaly=await judge_manual_save(session, payload.category, payload.value, confirmed=payload.confirm_anomaly),
        client_request_id=str(payload.client_request_id) if payload.client_request_id else None,
    )
    await _end_read_snapshot(session)
    session.add(record)
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race with another send of this request, still running when the client retried.
        await session.rollback()
        existing = await _already_created(session, user_id, payload)
        if existing is None:
            raise
        return RecordOut.from_record(existing, username)
    audit("record_created", "record created", user=user, record_id=record.id)
    return RecordOut.from_record(record, username)


def _problem(field: str, message: str) -> dict[str, str]:
    return {"field": f"query.{field}", "message": message, "type": "range_reversed"}


def _filter_conditions(params: RecordListParams) -> list[ColumnElement[bool]]:
    """All list filters. Each one is a single condition, and they combine with AND."""
    problems = []
    if params.value_min is not None and params.value_max is not None and params.value_min > params.value_max:
        problems.append(_problem("value_max", "數值上限不可小於下限"))
    if params.time_from is not None and params.time_to is not None and params.time_from > params.time_to:
        problems.append(_problem("time_to", "結束時間不可早於開始時間"))
    if problems:
        raise FieldValidationError(problems)

    conditions: list[ColumnElement[bool]] = []
    title = (params.title or "").strip()
    category = (params.category or "").strip()
    if title:
        conditions.append(Record.title.contains(title, autoescape=True))
    if category:
        conditions.append(Record.category == category)
    if params.value_min is not None:
        conditions.append(Record.value >= params.value_min)
    if params.value_max is not None:
        conditions.append(Record.value <= params.value_max)
    if params.time_from is not None:
        conditions.append(Record.timestamp >= params.time_from)
    if params.time_to is not None:
        conditions.append(Record.timestamp <= params.time_to)
    if params.anomaly_only:
        conditions.append(Record.is_anomaly.is_(True))
    return conditions


async def list_records(session: AsyncSession, params: RecordListParams) -> RecordPage:
    conditions = _filter_conditions(params)
    page_conditions = list(conditions)
    if params.cursor is not None:
        key, record_id = decode_cursor(params.cursor, params.sort_by, params.order)
        page_conditions.append(seek_condition(params.sort_by, params.order, key, record_id))

    total = await session.scalar(select(func.count()).select_from(Record).where(*conditions))
    # One extra row tells whether another page exists.
    rows = list(
        await session.scalars(
            select(Record)
            .options(joinedload(Record.creator))
            .where(*page_conditions)
            .order_by(*order_by_clause(params.sort_by, params.order))
            .limit(params.limit + 1)
        )
    )
    page = rows[: params.limit]
    has_next = len(rows) > params.limit
    return RecordPage(
        items=[RecordOut.from_record(row, row.creator.username if row.creator else None) for row in page],
        total=total or 0,
        limit=params.limit,
        next_cursor=encode_cursor(params.sort_by, params.order, page[-1]) if has_next else None,
    )


async def list_categories(session: AsyncSession) -> list[str]:
    return list(await session.scalars(select(Record.category).distinct().order_by(Record.category)))


async def check_anomaly(session: AsyncSession, params: AnomalyCheckParams) -> AnomalyCheck:
    category_range = await get_range(session, params.category)
    unit = await session.scalar(select(CategorySetting.unit).where(CategorySetting.category == params.category))
    return AnomalyCheck(
        category=params.category,
        value=params.value,
        would_be_anomaly=is_out_of_range(params.value, category_range),
        range=category_range,
        unit=unit,
    )


async def get_record(session: AsyncSession, record_id: int) -> RecordOut:
    record = await session.scalar(select(Record).options(joinedload(Record.creator)).where(Record.id == record_id))
    if record is None:
        raise RecordNotFoundError(details={"record_id": record_id})
    return RecordOut.from_record(record, record.creator.username if record.creator else None)


async def _locked_for_change(session: AsyncSession, user: User, record_id: int) -> Record:
    await _end_read_snapshot(session)
    # The row lock settles concurrent changes: the loser of two deletes sees 404, not a
    # failure, and the checks below cannot be outdated by the time we write.
    record = await session.scalar(select(Record).where(Record.id == record_id).with_for_update())
    if record is None:
        raise RecordNotFoundError(details={"record_id": record_id})
    if not user_can(user, Capability.RECORDS_MODIFY_ANY) and record.created_by_user_id != user.id:
        audit(
            "permission_denied",
            "permission denied",
            user=user,
            level=logging.WARNING,
            role=user.role.value,
            reason="not_record_owner",
            record_id=record.id,
        )
        raise NotRecordOwnerError(details={"record_id": record.id})
    return record


async def update_record(session: AsyncSession, user: User, record_id: int, payload: RecordUpdate) -> RecordOut:
    record = await _locked_for_change(session, user, record_id)
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True, exclude={"confirm_anomaly"})
    new_value = changes.get("value", record.value)
    new_category = changes.get("category", record.category)
    # PRD-09-R12: re-judge only when the value or category really changes. The edit form sends
    # every field, so a title-only edit must not change the record's anomaly flag just
    # because the range moved since it was saved. This is a working interpretation: to re-judge
    # on every edit instead, remove this condition.
    if new_value != record.value or new_category != record.category:
        changes["is_anomaly"] = await judge_manual_save(
            session, new_category, new_value, confirmed=payload.confirm_anomaly
        )
    for field, value in changes.items():
        setattr(record, field, value)
    creator_name = await _creator_name(session, record)
    await session.commit()
    # Field names only: earlier content must not survive anywhere (PRD-05-R9).
    audit("record_updated", "record updated", user=user, record_id=record.id, fields=sorted(changes.keys() - {"is_anomaly"}))
    return RecordOut.from_record(record, creator_name)


async def delete_record(session: AsyncSession, user: User, record_id: int) -> None:
    record = await _locked_for_change(session, user, record_id)
    await session.delete(record)
    await session.commit()
    # No content: a deleted record cannot be recovered (PRD-05-R7).
    audit("record_deleted", "record deleted", user=user, record_id=record_id)


async def _creator_name(session: AsyncSession, record: Record) -> str | None:
    if record.created_by_user_id is None:
        return None
    creator = await session.get(User, record.created_by_user_id)
    return creator.username if creator else None
