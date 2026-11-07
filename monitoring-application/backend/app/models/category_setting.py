import enum

from sqlalchemy import CheckConstraint, Double, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.record import CATEGORY_COLLATION, CATEGORY_MAX_LENGTH, TABLE_COLLATION

UNIT_MAX_LENGTH = 32


class SettingOrigin(enum.StrEnum):
    DEFAULT = "default"
    ADMIN = "admin"


def _origin_column(name: str) -> Mapped[SettingOrigin | None]:
    return mapped_column(
        Enum(SettingOrigin, name=name, values_callable=lambda origins: [origin.value for origin in origins]),
        nullable=True,
    )


class CategorySetting(Base):
    """The range and unit of one category. Same key as records.category, without a foreign key,
    because a setting can exist while the category has no records (PRD-07-R8)."""

    __tablename__ = "category_settings"
    __table_args__ = (
        # Spelled out in full: "range_min < range_max" alone lets one NULL through.
        CheckConstraint(
            "(range_min IS NULL AND range_max IS NULL)"
            " OR (range_min IS NOT NULL AND range_max IS NOT NULL AND range_min < range_max)",
            name="range_pair",
        ),
        {"mariadb_charset": "utf8mb4", "mariadb_collate": TABLE_COLLATION},
    )

    category: Mapped[str] = mapped_column(String(CATEGORY_MAX_LENGTH, collation=CATEGORY_COLLATION), primary_key=True)
    range_min: Mapped[float | None] = mapped_column(Double, nullable=True)
    range_max: Mapped[float | None] = mapped_column(Double, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(UNIT_MAX_LENGTH), nullable=True)
    # NULL: never configured, so a default may still apply. Otherwise who configured it last.
    # "admin" with an empty value is a cleared setting, which a default must not bring back
    # (PRD-07-R8, R11; PRD-17-R9). Range and unit are tracked apart for the same reason.
    range_origin: Mapped[SettingOrigin | None] = _origin_column("category_setting_range_origin")
    unit_origin: Mapped[SettingOrigin | None] = _origin_column("category_setting_unit_origin")
