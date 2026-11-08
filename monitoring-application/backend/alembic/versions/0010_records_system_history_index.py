"""Index for the Admin history of system records (PRD-16, LB-336): the creator filter, the time
range and the category and anomaly filters are all read from the index.

Measured with tests/test_history_volume.py on about 4.2M system rows (a simulator year), latest
3 months, all categories: the total count went from a full table scan (about 1.2 s per page
request) to a range scan of this index (about 0.3 s).

MariaDB drops the foreign key's implicit index on created_by_user_id once this index, which
starts with that column, exists, and then refuses to drop this one while the foreign key needs
it. Downgrade therefore drops and re-adds the foreign key around it, which gives the key back its
own implicit index, exactly as at 0009.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

INDEX = "ix_records_creator_timestamp_category"
FOREIGN_KEY = "fk_records_created_by_user_id_users"


def upgrade() -> None:
    op.create_index(INDEX, "records", ["created_by_user_id", "timestamp", "category", "is_anomaly"])


def downgrade() -> None:
    op.drop_constraint(FOREIGN_KEY, "records", type_="foreignkey")
    op.drop_index(INDEX, table_name="records")
    op.create_foreign_key(FOREIGN_KEY, "records", "users", ["created_by_user_id"], ["id"], ondelete="RESTRICT")
