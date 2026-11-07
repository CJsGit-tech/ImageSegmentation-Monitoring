"""Startup defaults for simulated devices (PRD-07-R8, R11). LB-330 decides the actual values."""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.events import audit
from app.categories.service import lock_setting
from app.models.category_setting import SettingOrigin


@dataclass(frozen=True)
class CategoryDefault:
    """Leave a value as None to provide no default for it. Give both bounds or neither."""

    category: str
    range_min: float | None = None
    range_max: float | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if (self.range_min is None) != (self.range_max is None):
            raise ValueError(f"{self.category}: give both range bounds or neither")
        if self.range_min is not None and self.range_min >= self.range_max:
            raise ValueError(f"{self.category}: range_min must be below range_max")


async def ensure_category_defaults(engine: AsyncEngine, defaults: Iterable[CategoryDefault]) -> None:
    """Applies each default only if that value was never configured, so call it on every start.

    A range or unit that an Admin changed or cleared is left alone, as is one already seeded.
    Range and unit are independent: a unit set by an Admin does not stop the default range.
    Safe to run from several backends at once: each category is settled under its row lock.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:
        for default in defaults:
            setting = await lock_setting(session, default.category)
            seeded: dict[str, object] = {}
            if default.range_min is not None and setting.range_origin is None:
                setting.range_min, setting.range_max = default.range_min, default.range_max
                setting.range_origin = SettingOrigin.DEFAULT
                seeded |= {"new_min": default.range_min, "new_max": default.range_max}
            if default.unit is not None and setting.unit_origin is None:
                setting.unit = default.unit
                setting.unit_origin = SettingOrigin.DEFAULT
                seeded["new_unit"] = default.unit
            await session.commit()
            if seeded:
                audit("category_defaults_seeded", "category defaults seeded", category=setting.category, **seeded)
