from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.record import TABLE_COLLATION

FILENAME_MAX_LENGTH = 255
FILENAME_KEY_COLLATION = "utf8mb4_bin"


class RecordImport(Base):
    """One successful bulk import (PRD-06). Written in the same transaction as its records, so
    a refused or failed upload leaves no row and its filename stays free (PRD-06-R7)."""

    __tablename__ = "record_imports"
    __table_args__ = ({"mariadb_charset": "utf8mb4", "mariadb_collate": TABLE_COLLATION},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # As uploaded, for display.
    filename: Mapped[str] = mapped_column(String(FILENAME_MAX_LENGTH), nullable=False)
    # Normalised in Python (base name, trimmed, NFC, casefolded) and compared byte for byte, so
    # the rule lives in one place: app.records.importing.filename_key.
    filename_key: Mapped[str] = mapped_column(
        String(FILENAME_MAX_LENGTH, collation=FILENAME_KEY_COLLATION), nullable=False, unique=True
    )
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    anomaly_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # Naive UTC with microseconds.
    imported_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False)
