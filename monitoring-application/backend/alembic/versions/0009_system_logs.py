"""System log table, written from the audit events and queried by Admin (PRD-14).

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("occurred_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("info", "warning", "error", name="system_log_severity"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.String(length=255), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_logs")),
        mariadb_charset="utf8mb4",
        mariadb_collate="utf8mb4_uca1400_ai_ci",
    )
    op.create_index("ix_system_logs_occurred_at", "system_logs", ["occurred_at"])
    op.create_index("ix_system_logs_event_type_occurred_at", "system_logs", ["event_type", "occurred_at"])
    op.create_index("ix_system_logs_severity_occurred_at", "system_logs", ["severity", "occurred_at"])
    op.create_index("ix_system_logs_username_occurred_at", "system_logs", ["username", "occurred_at"])


def downgrade() -> None:
    op.drop_table("system_logs")
