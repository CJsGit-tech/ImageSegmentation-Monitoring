"""Route dependencies. Protect endpoints with these rather than checking roles by hand."""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.errors import NotAuthenticatedError
from app.auth.events import audit
from app.auth.permissions import CAPABILITY_LABELS, CAPABILITY_ROLES, Capability, user_can
from app.auth.service import authenticate_token
from app.core.config import get_settings
from app.core.errors import ErrorResponse, ForbiddenError
from app.db.session import DbSession
from app.models.user import User

# auto_error is off so that a missing token gets the unified error format.
_bearer = HTTPBearer(auto_error=False, description="POST /api/v1/auth/login 取得的 access_token")

UNAUTHENTICATED_RESPONSE: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ErrorResponse,
        "description": "未登入或登入失效，error.code 為 UNAUTHORIZED、TOKEN_EXPIRED、SESSION_SUPERSEDED 或 SESSION_LOGGED_OUT",
    },
}
FORBIDDEN_RESPONSE: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse, "description": "角色沒有此操作的權限，error.message 說明原因"},
}


async def get_current_user(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise NotAuthenticatedError()
    return await authenticate_token(session, credentials.credentials, get_settings())


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_capability(capability: Capability) -> Callable[..., Awaitable[User]]:
    """Use as Depends(require_capability(Capability.RECORDS_CREATE)). Returns the current user."""

    async def check(user: CurrentUser) -> User:
        if not user_can(user, capability):
            audit(
                "permission_denied",
                "permission denied",
                user=user,
                level=logging.WARNING,
                role=user.role.value,
                capability=capability.value,
            )
            allowed_roles = [role.value for role in CAPABILITY_ROLES[capability]]
            raise ForbiddenError(
                f"您的角色（{user.role.value}）不能{CAPABILITY_LABELS[capability]}，此操作僅限 {'、'.join(allowed_roles)}",
                details={
                    "required_capability": capability.value,
                    "current_role": user.role.value,
                    "allowed_roles": allowed_roles,
                },
            )
        return user

    return check
