"""Real-time data history (PRD-16): raw system-generated records for tracing, Admin only.
Read only and without export (PRD-16-R4, R8). Query logic: app.history.service."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.permissions import Capability
from app.core.errors import UNAVAILABLE_RESPONSE, ErrorResponse
from app.db.session import DbSession
from app.history import service
from app.history.schemas import HistoryQuery
from app.models.user import User
from app.records.schemas import RecordPage

router = APIRouter(
    prefix="/history",
    tags=["history"],
    responses={**UNAVAILABLE_RESPONSE, **UNAUTHENTICATED_RESPONSE, **FORBIDDEN_RESPONSE},
)

Admin = Annotated[User, Depends(require_capability(Capability.SYSTEM_ADMIN))]


@router.get(
    "/records",
    summary="即時資料歷史：系統產生的原始記錄，依時間範圍（必填，最長 3 個月）、分類、僅異常過濾，分頁（限 Admin）",
    description=(
        "只含系統產生的記錄（creator_id 為 null、creator_name 為「系統」）；手動新增與導入的記錄不在此列，請用 GET /records。"
        "Admin 修改過的系統記錄仍列出，內容為目前的版本。start、end 必填，為含時區位移的 ISO-8601，兩端都包含；"
        "start 可等於 end，晚於 end 時回 422 VALIDATION_ERROR（query.end、range_reversed）。end 不可晚於 start 的 "
        "3 個日曆月後（剛好 3 個月可以查詢），超過時回 422 RANGE_TOO_LONG，details 含 max_months 與 latest_allowed_end。"
        "category 可重複，省略表示全部分類；anomaly_only 為 true 時只列異常。條件可同時使用。"
        "依時間戳排序（order：desc 新到舊為預設，或 asc），時間相同時依 id；以 next_cursor 接續下一頁，須搭配相同的條件與 order。"
        "items 為空、total 為 0 時顯示空狀態說明。單位與範圍請用 GET /category-display（目前的設定）。此頁沒有匯出。"
    ),
    responses={422: {"model": ErrorResponse, "description": "VALIDATION_ERROR 或 RANGE_TOO_LONG"}},
)
async def history_records(params: Annotated[HistoryQuery, Query()], _: Admin, session: DbSession) -> RecordPage:
    return await service.history_page(session, params)
