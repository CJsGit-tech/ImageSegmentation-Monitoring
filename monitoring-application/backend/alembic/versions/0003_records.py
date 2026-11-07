"""Data records.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("category", sa.String(length=100, collation="utf8mb4_uca1400_as_cs"), nullable=False),
        sa.Column("timestamp", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("is_anomaly", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_records_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_records")),
        mariadb_charset="utf8mb4",
        mariadb_collate="utf8mb4_uca1400_ai_ci",
    )
    op.create_index(op.f("ix_records_title"), "records", ["title"])
    op.create_index(op.f("ix_records_value"), "records", ["value"])
    op.create_index(op.f("ix_records_category"), "records", ["category"])
    op.create_index(op.f("ix_records_timestamp"), "records", ["timestamp"])
    op.create_index("ix_records_category_timestamp", "records", ["category", "timestamp"])


def downgrade() -> None:
    op.drop_table("records")
