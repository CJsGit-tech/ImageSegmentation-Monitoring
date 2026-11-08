from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.permissions import Capability
from app.core.errors import UNAVAILABLE_RESPONSE, ErrorResponse
from app.db.session import DbSession
from app.models.user import User
from app.users import service
from app.users.schemas import ManagedUserOut, RoleChange, UserListParams, UserPage

_ROLE_CHANGE_FORBIDDEN_RESPONSE: dict[int | str, dict[str, Any]] = {
    403: {
        "model": ErrorResponse,
        "description": (
            "不是 Admin（FORBIDDEN），或嘗試變更自己的角色（OWN_ROLE_CHANGE_FORBIDDEN），error.message 說明原因"
        ),
    },
}
_NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "使用者不存在，error.code 為 USER_NOT_FOUND"},
}

# Only listing and role changes: this version has no way to disable or delete accounts (PRD-13-R6).
router = APIRouter(
    prefix="/users", tags=["users"], responses={**UNAVAILABLE_RESPONSE, **UNAUTHENTICATED_RESPONSE}
)

Admin = Annotated[User, Depends(require_capability(Capability.SYSTEM_ADMIN))]
UserId = Annotated[int, Path(ge=1, le=2**63 - 1)]


@router.get("", summary="使用者列表：依帳號搜尋、分頁（限 Admin）", responses=FORBIDDEN_RESPONSE)
async def list_users(params: Annotated[UserListParams, Query()], admin: Admin, session: DbSession) -> UserPage:
    return await service.list_users(session, admin, params)


@router.patch(
    "/{user_id}/role",
    summary="變更使用者角色（限 Admin，不能變更自己）；於該使用者下一次請求生效",
    responses={**_ROLE_CHANGE_FORBIDDEN_RESPONSE, **_NOT_FOUND_RESPONSE},
)
async def change_role(user_id: UserId, payload: RoleChange, admin: Admin, session: DbSession) -> ManagedUserOut:
    return await service.change_role(session, admin, user_id, payload.role)
