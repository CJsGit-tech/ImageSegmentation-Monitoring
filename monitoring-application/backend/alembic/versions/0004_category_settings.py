"""Category ranges and units, and the index behind the anomaly filter.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_ORIGINS = ("default", "admin")


def upgrade() -> None:
    op.create_table(
        "category_settings",
        sa.Column("category", sa.String(length=100, collation="utf8mb4_uca1400_as_cs"), nullable=False),
        sa.Column("range_min", sa.Double(), nullable=True),
        sa.Column("range_max", sa.Double(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("range_origin", sa.Enum(*_ORIGINS, name="category_setting_range_origin"), nullable=True),
        sa.Column("unit_origin", sa.Enum(*_ORIGINS, name="category_setting_unit_origin"), nullable=True),
        sa.CheckConstraint(
            "(range_min IS NULL AND range_max IS NULL)"
            " OR (range_min IS NOT NULL AND range_max IS NOT NULL AND range_min < range_max)",
            name=op.f("ck_category_settings_range_pair"),
        ),
        sa.PrimaryKeyConstraint("category", name=op.f("pk_category_settings")),
        mariadb_charset="utf8mb4",
        mariadb_collate="utf8mb4_uca1400_ai_ci",
    )
    op.create_index("ix_records_is_anomaly_timestamp", "records", ["is_anomaly", "timestamp"])


def downgrade() -> None:
    op.drop_index("ix_records_is_anomaly_timestamp", table_name="records")
    op.drop_table("category_settings")
