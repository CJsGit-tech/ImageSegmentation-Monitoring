"""Covering index for data analysis: per-category statistics and trends read only the index.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_records_category_timestamp_value", "records", ["category", "timestamp", "value", "is_anomaly"]
    )


def downgrade() -> None:
    op.drop_index("ix_records_category_timestamp_value", table_name="records")
