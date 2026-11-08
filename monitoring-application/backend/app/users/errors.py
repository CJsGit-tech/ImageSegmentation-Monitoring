"""User management failures. Clients branch on these stable error.code values."""

from app.core.errors import ForbiddenError, NotFoundError


class UserNotFoundError(NotFoundError):
    code = "USER_NOT_FOUND"
    message = "這位使用者已不存在"


class OwnRoleChangeForbiddenError(ForbiddenError):
    code = "OWN_ROLE_CHANGE_FORBIDDEN"
    message = "不能變更自己的角色；Admin 的角色只能由另一位 Admin 調整"
