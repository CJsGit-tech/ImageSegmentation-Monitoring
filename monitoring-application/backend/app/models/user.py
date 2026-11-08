import enum
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Account names compare case and accent insensitively under this collation.
USERNAME_COLLATION = "utf8mb4_uca1400_ai_ci"
USERNAME_MAX_LENGTH = 64


class Role(enum.StrEnum):
    ADMIN = "Admin"
    USER = "User"
    VIEWER = "Viewer"


def _utc_now() -> datetime:
    """Naive UTC at whole seconds, which is what the DATETIME column stores."""
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"mariadb_charset": "utf8mb4", "mariadb_collate": USERNAME_COLLATION}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(USERNAME_MAX_LENGTH), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="user_role", values_callable=lambda roles: [role.value for role in roles]),
        nullable=False,
        default=Role.VIEWER,
    )
    # The only login that is valid. A new login overwrites it and logout clears it.
    current_session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utc_now)
    # Set by each successful login. Null means the account has not logged in since the column was added.
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
