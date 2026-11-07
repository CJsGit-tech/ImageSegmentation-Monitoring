"""Model registry. Import each feature model here so that Alembic can see its table."""

from app.db.base import Base
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.record_import import RecordImport
from app.models.user import Role, User

__all__ = ["Base", "CategorySetting", "Record", "Role", "User"]
