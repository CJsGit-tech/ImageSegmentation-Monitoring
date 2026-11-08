"""Data analysis endpoints (PRD-10, PRD-11). Query logic: app.analytics.service."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.analytics import service
from app.analytics.schemas import AnalysisQuery, AnomalyListQuery, StatisticsResult, TrendQuery, TrendResult
from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.permissions import Capability
from app.core.errors import UNAVAILABLE_RESPONSE, ErrorResponse
from app.db.session import DbSession
from app.models.user import User
from app.records.schemas import RecordPage

_COMMON = (
    "所有已登入角色可用。start、end 為含時區位移的 ISO-8601，兩端都包含；start 可等於 end，晚於 end 時回 422 "
    "VALIDATION_ERROR（query.end、range_reversed）。end 不可晚於 start 的 3 個日曆月後（以 start 的時區計算，"
    "日期超過月底時取月底；剛好 3 個月可以查詢），超過時回 422 RANGE_TOO_LONG，details 含 max_months 與 "
    "latest_allowed_end。category 可重複，省略表示全部分類。涵蓋系統產生、手動新增與導入的記錄。"
)

_RANGE_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "VALIDATION_ERROR 或 RANGE_TOO_LONG"},
}

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    responses={**UNAVAILABLE_RESPONSE, **UNAUTHENTICATED_RESPONSE, **FORBIDDEN_RESPONSE},
)

Analyst = Annotated[User, Depends(require_capability(Capability.ANALYSIS_VIEW))]


@router.get(
    "/statistics",
    summary="各分類各自的總計、平均、最大值、最小值（沒有跨分類合計，最長 3 個月）",
    description=_COMMON
    + " has_data 為 false 時顯示空狀態說明。count 僅供人工核對（各分類 count 加總 = GET /records 同範圍的 total），畫面不顯示。",
    responses=_RANGE_ERRORS,
)
async def statistics(params: Annotated[AnalysisQuery, Query()], _: Analyst, session: DbSession) -> StatisticsResult:
    return await service.category_statistics(session, params)


@router.get(
    "/trend",
    summary="各分類的趨勢資料點：依所選粒度取平均，含異常的區間保留異常標記（最長 3 個月）",
    description=_COMMON
    + f" granularity 為 minute、hour（預設）或 day，區間對齊 start 的時區位移（day 為當地午夜）。"
    f"範圍在所選粒度下每個分類超過 {service.MAX_POINTS_PER_CATEGORY} 個區間時，查詢前即回 422 TOO_MANY_POINTS，"
    "details 含 granularity、points_per_category、max_points 與 coarser（可改選的較粗粒度）。"
    "沒有資料的區間不列出。anomaly_count 大於 0 的區間以 anomaly_min／anomaly_max（實際異常數值）標示異常。",
    responses={422: {"model": ErrorResponse, "description": "VALIDATION_ERROR、RANGE_TOO_LONG 或 TOO_MANY_POINTS"}},
)
async def trend(params: Annotated[TrendQuery, Query()], _: Analyst, session: DbSession) -> TrendResult:
    return await service.trend(session, params)


@router.get(
    "/anomalies",
    summary="異常記錄清單：只列被判為異常的記錄，分頁（最長 3 個月）",
    description=_COMMON
    + " 回應格式同 GET /records。預設依時間由新到舊；分頁以 next_cursor 接續，須搭配相同的條件、sort_by 與 order。",
    responses=_RANGE_ERRORS,
)
async def anomalies(params: Annotated[AnomalyListQuery, Query()], _: Analyst, session: DbSession) -> RecordPage:
    return await service.anomaly_page(session, params)
