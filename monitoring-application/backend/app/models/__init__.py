"""Model registry. Import each feature model here so that Alembic can see its table."""

from app.db.base import Base
from app.models.user import Role, User

__all__ = ["Base", "Role", "User"]
