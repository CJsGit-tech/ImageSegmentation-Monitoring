"""Successful bulk imports, which hold the filename uniqueness rule (PRD-06-R7).

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "record_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("filename_key", sa.String(length=255, collation="utf8mb4_bin"), nullable=False),
        sa.Column("file_format", sa.String(length=8), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("anomaly_count", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("imported_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("imported_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"],
            ["users.id"],
            name=op.f("fk_record_imports_imported_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_record_imports")),
        sa.UniqueConstraint("filename_key", name=op.f("uq_record_imports_filename_key")),
        mariadb_charset="utf8mb4",
        mariadb_collate="utf8mb4_uca1400_ai_ci",
    )


def downgrade() -> None:
    op.drop_table("record_imports")
