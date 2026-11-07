"""Category ranges and units (PRD-17). The anomaly rule itself is in app.records.anomaly."""

from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.events import audit
from app.categories.schemas import (
    CategoryDisplayList,
    CategoryDisplayOut,
    CategorySettingList,
    CategorySettingOut,
    CategorySettingUpdate,
)
from app.models.category_setting import CategorySetting, SettingOrigin
from app.models.record import Record
from app.models.user import User
from app.records.anomaly import CategoryRange


class _Listed(NamedTuple):
    category: str
    range_min: float | None
    range_max: float | None
    unit: str | None
    has_data: bool

    @property
    def range(self) -> CategoryRange | None:
        return None if self.range_min is None else CategoryRange(min=self.range_min, max=self.range_max)


async def _listed_categories(session: AsyncSession) -> list[_Listed]:
    """Categories with data, a range or a unit. Joined in the database, whose collation decides
    which spellings are the same category."""
    data = select(Record.category).distinct().subquery()
    with_setting = await session.execute(
        select(
            CategorySetting.category,
            CategorySetting.range_min,
            CategorySetting.range_max,
            CategorySetting.unit,
            data.c.category.is_not(None).label("has_data"),
        )
        .outerjoin(data, data.c.category == CategorySetting.category)
        .where(data.c.category.is_not(None) | CategorySetting.range_min.is_not(None) | CategorySetting.unit.is_not(None))
    )
    without_setting = await session.scalars(
        select(data.c.category)
        .outerjoin(CategorySetting, CategorySetting.category == data.c.category)
        .where(CategorySetting.category.is_(None))
    )
    return [
        *(_Listed(row.category, row.range_min, row.range_max, row.unit, bool(row.has_data)) for row in with_setting),
        *(_Listed(category, None, None, None, True) for category in without_setting),
    ]


def _setting_out(item: _Listed) -> CategorySettingOut:
    return CategorySettingOut(
        category=item.category,
        range=item.range,
        range_status="unset" if item.range is None else "set",
        unit=item.unit,
        has_data=item.has_data,
    )


async def list_settings(session: AsyncSession) -> CategorySettingList:
    items = sorted(await _listed_categories(session), key=lambda item: (item.range is not None, item.category))
    return CategorySettingList(
        items=[_setting_out(item) for item in items],
        unset_count=sum(item.range is None for item in items),
    )


async def list_display(session: AsyncSession) -> CategoryDisplayList:
    items = sorted(await _listed_categories(session), key=lambda item: item.category)
    return CategoryDisplayList(
        items=[CategoryDisplayOut(category=item.category, range=item.range, unit=item.unit) for item in items]
    )


async def lock_setting(session: AsyncSession, category: str) -> CategorySetting:
    """The category's row, created if absent and locked until the next commit.

    The session must hold no read snapshot: see _end_read_snapshot in app.records.service.
    Insert first, because FOR UPDATE on a missing row takes a gap lock, and two of those
    deadlock as soon as both sides insert.
    """
    await session.execute(
        insert(CategorySetting).values(category=category).on_duplicate_key_update(category=CategorySetting.category)
    )
    return await session.scalar(
        select(CategorySetting)
        .where(CategorySetting.category == category)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _event_verb(old: object, new: object) -> str:
    if old is None:
        return "set"
    return "cleared" if new is None else "changed"


async def update_setting(session: AsyncSession, user: User, payload: CategorySettingUpdate) -> CategorySettingOut:
    # Ends the read snapshot left by authentication, so the locking read below cannot fail
    # with error 1020 when another Admin just changed this row.
    await session.commit()
    setting = await lock_setting(session, payload.category)

    events = []
    if "range" in payload.model_fields_set:
        old = (setting.range_min, setting.range_max)
        new = (payload.range.min, payload.range.max) if payload.range else (None, None)
        if new != old:
            setting.range_min, setting.range_max = new
            setting.range_origin = SettingOrigin.ADMIN
            events.append(
                (
                    f"category_range_{_event_verb(old[0], new[0])}",
                    {"old_min": old[0], "old_max": old[1], "new_min": new[0], "new_max": new[1]},
                )
            )
    if "unit" in payload.model_fields_set and payload.unit != setting.unit:
        events.append((f"category_unit_{_event_verb(setting.unit, payload.unit)}", {"old_unit": setting.unit, "new_unit": payload.unit}))
        setting.unit = payload.unit
        setting.unit_origin = SettingOrigin.ADMIN

    # Before the commit, so that the answer describes the row this request wrote.
    has_data = await session.scalar(select(Record.id).where(Record.category == payload.category).limit(1)) is not None
    await session.commit()
    for event_type, fields in events:
        audit(event_type, event_type.replace("_", " "), user=user, category=setting.category, **fields)
    return _setting_out(_Listed(setting.category, setting.range_min, setting.range_max, setting.unit, has_data))
