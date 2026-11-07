"""One-time key on record creation, so that a retry cannot add a second record.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("records", sa.Column("client_request_id", sa.String(length=36), nullable=True))
    op.create_unique_constraint("uq_records_client_request", "records", ["client_request_id", "created_by_user_id"])


def downgrade() -> None:
    op.drop_constraint("uq_records_client_request", "records", type_="unique")
    op.drop_column("records", "client_request_id")
