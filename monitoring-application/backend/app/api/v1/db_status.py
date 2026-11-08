from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.permissions import Capability
from app.core.config import get_settings
from app.core.errors import ErrorResponse
from app.db.session import DbSession
from app.dbstatus.schemas import DbStatus
from app.dbstatus.service import DbStatusMonitor, get_monitor, get_status_engine
from app.models.user import User

_UNAVAILABLE = {
    503: {
        "model": ErrorResponse,
        "description": (
            "登入驗證需要讀取資料庫；資料庫在驗證時就無法使用，回 503，error.code 為 DATABASE_UNAVAILABLE。"
            "畫面應把它與 200 的 status=unavailable 一樣顯示為不可用"
        ),
    },
}

router = APIRouter(
    prefix="/db-status",
    tags=["system"],
    responses={**_UNAVAILABLE, **UNAUTHENTICATED_RESPONSE, **FORBIDDEN_RESPONSE},
)

Admin = Annotated[User, Depends(require_capability(Capability.SYSTEM_ADMIN))]


@router.get(
    "",
    summary="資料庫狀態：連線、回應時間、連線池、資料筆數、最後一次批次寫入時間（限 Admin）",
    description=(
        "供畫面每 10 秒呼叫。驗證通過後，對資料庫的檢查最多等 2 秒；檢查失敗仍回 200，"
        "status 為 unavailable，error.category 說明原因（不含資料庫原始錯誤文字），指標為 null。"
        "資料筆數為精確值，每個後端程序最多每 60 秒重算一次。"
    ),
)
async def db_status(
    _: Admin,
    session: DbSession,
    engine: Annotated[AsyncEngine, Depends(get_status_engine)],
    monitor: Annotated[DbStatusMonitor, Depends(get_monitor)],
) -> DbStatus:
    # Hands the login check's connection back first, so it is not counted as in use.
    await session.rollback()
    settings = get_settings()
    return await monitor.check(
        engine,
        pool_capacity=settings.db_pool_size + settings.db_max_overflow,
        simulator_interval_seconds=settings.simulator_interval_seconds,
    )
