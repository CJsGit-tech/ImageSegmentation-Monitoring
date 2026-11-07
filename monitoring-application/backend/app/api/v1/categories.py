from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.permissions import Capability
from app.categories import service
from app.categories.schemas import CategoryDisplayList, CategorySettingList, CategorySettingOut, CategorySettingUpdate
from app.core.errors import UNAVAILABLE_RESPONSE
from app.db.session import DbSession
from app.models.user import User

router = APIRouter(tags=["categories"], responses={**UNAVAILABLE_RESPONSE, **UNAUTHENTICATED_RESPONSE})

Admin = Annotated[User, Depends(require_capability(Capability.SYSTEM_ADMIN))]
Reader = Annotated[User, Depends(require_capability(Capability.RECORDS_READ))]


@router.get("/category-settings", summary="異常範圍設定：列出分類的範圍與單位（僅 Admin）", responses=FORBIDDEN_RESPONSE)
async def list_settings(_: Admin, session: DbSession) -> CategorySettingList:
    return await service.list_settings(session)


@router.patch(
    "/category-settings",
    summary="設定、修改或清除分類的範圍與單位（僅 Admin）；只影響之後的資料",
    responses=FORBIDDEN_RESPONSE,
)
async def update_setting(payload: CategorySettingUpdate, user: Admin, session: DbSession) -> CategorySettingOut:
    return await service.update_setting(session, user, payload)


@router.get("/category-display", summary="供畫面顯示的分類範圍與單位（唯讀，所有已登入角色）", responses=FORBIDDEN_RESPONSE)
async def list_display(_: Reader, session: DbSession) -> CategoryDisplayList:
    return await service.list_display(session)
