from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.permissions import Capability
from app.core.config import get_settings
from app.core.errors import UNAVAILABLE_RESPONSE
from app.db.session import DbSession
from app.models.user import User
from app.syslog import service
from app.syslog.schemas import LogOptions, LogPage, LogQuery

# Read only (PRD-14-R3): there is no way to add, change or delete log rows through the API.
router = APIRouter(
    prefix="/logs",
    tags=["system-logs"],
    responses={**UNAVAILABLE_RESPONSE, **UNAUTHENTICATED_RESPONSE, **FORBIDDEN_RESPONSE},
)

Admin = Annotated[User, Depends(require_capability(Capability.SYSTEM_ADMIN))]


@router.get(
    "",
    summary="查詢系統日誌：時間範圍（最長 3 個月）、事件類型、嚴重程度、使用者；由新到舊分頁（限 Admin）",
    description=(
        "時間範圍超過 3 個月回 422，error.code 為 RANGE_TOO_LONG（剛好 3 個月可以）；"
        "開始不早於結束回 422 range_reversed。日誌保留期限由 LOG_RETENTION_DAYS 設定，超過的自動清理。"
    ),
)
async def list_logs(params: Annotated[LogQuery, Query()], _: Admin, session: DbSession) -> LogPage:
    return await service.list_logs(session, params)


@router.get("/options", summary="系統日誌的篩選選項：事件類型、嚴重程度與保留天數（限 Admin）")
async def log_options(_: Admin) -> LogOptions:
    return service.options(get_settings().log_retention_days)
