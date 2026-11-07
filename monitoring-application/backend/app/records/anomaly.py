"""The only definition of what is anomalous (PRD-09-R7) and of what that makes a record (PRD-18)."""

import logging

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.events import audit
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.user import User
from app.records.errors import AnomalyConfirmationRequiredError, AnomalyRecordReadOnlyError


class CategoryRange(BaseModel):
    min: float
    max: float


def is_out_of_range(value: float, category_range: CategoryRange | None) -> bool:
    """Touching a bound counts. A category with no range is never judged (PRD-09-R9)."""
    if category_range is None:
        return False
    return value <= category_range.min or value >= category_range.max


async def get_range(session: AsyncSession, category: str) -> CategoryRange | None:
    """The category's range in this session's snapshot.

    Bulk writers and the simulator use this with is_out_of_range. Look up each spelling here
    rather than matching strings in Python: the database collation decides what is the same
    category. A long-lived session must commit between reads, or it keeps seeing old ranges.
    """
    # Columns, not the entity: an entity already in the session would keep its old values.
    row = (
        await session.execute(
            select(CategorySetting.range_min, CategorySetting.range_max).where(CategorySetting.category == category)
        )
    ).first()
    if row is None or row.range_min is None:
        return None
    return CategoryRange(min=row.range_min, max=row.range_max)


async def evaluate_anomaly(session: AsyncSession, category: str, value: float) -> bool:
    return is_out_of_range(value, await get_range(session, category))


async def judge_manual_save(session: AsyncSession, category: str, value: float, *, confirmed: bool) -> bool:
    """The save-time judgement, which is final (PRD-09-R4). An anomaly becomes permanent, so a
    person must have confirmed it (PRD-18-R6): whatever the check said earlier, this refuses
    an unconfirmed one. A confirmation is ignored when the value turns out to be normal."""
    category_range = await get_range(session, category)
    if not is_out_of_range(value, category_range):
        return False
    if not confirmed:
        raise AnomalyConfirmationRequiredError(
            details={"category": category, "value": value, "range": category_range.model_dump()}
        )
    return True


def ensure_modifiable(record: Record, user: User) -> None:
    """Called with the row locked, before any update or delete, for every role (PRD-18-R1)."""
    if record.is_anomaly:
        audit(
            "permission_denied",
            "permission denied",
            user=user,
            level=logging.WARNING,
            role=user.role.value,
            reason="anomaly_read_only",
            record_id=record.id,
        )
        raise AnomalyRecordReadOnlyError(details={"record_id": record.id})
