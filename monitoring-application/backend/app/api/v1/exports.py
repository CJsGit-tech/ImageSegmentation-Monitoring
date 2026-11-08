"""Excel download of the data analysis results (PRD-12). Logic: app.exports.service."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.analytics.schemas import AnalysisQuery
from app.api.v1.analytics import _COMMON
from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.permissions import Capability
from app.core.errors import UNAVAILABLE_RESPONSE, ErrorResponse
from app.db.session import DbSession
from app.exports import service
from app.exports.excel import MAX_DETAIL_ROWS, XLSX_MEDIA_TYPE
from app.models.user import User

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    responses={**UNAVAILABLE_RESPONSE, **UNAUTHENTICATED_RESPONSE, **FORBIDDEN_RESPONSE},
)

Analyst = Annotated[User, Depends(require_capability(Capability.ANALYSIS_VIEW))]

_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "Excel 檔（.xlsx）。Content-Disposition 為 attachment，檔名含產生的日期時間（start 的時區），"
            "例如 資料分析_20261107-153000.xlsx（filename* 以 UTF-8 編碼，filename 為 ASCII 的 analysis_…）。"
        ),
        "content": {XLSX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
    },
    404: {"model": ErrorResponse, "description": "EXPORT_NO_DATA：條件內沒有資料，不產生檔案（PRD-12-R4）"},
    422: {
        "model": ErrorResponse,
        "description": f"VALIDATION_ERROR、RANGE_TOO_LONG，或 EXPORT_TOO_LARGE（明細超過 {MAX_DETAIL_ROWS:,} 筆，details 含 rows 與 max_rows）",
    },
}


@router.get(
    "/export",
    summary="下載 Excel：摘要（查詢條件與各分類四項統計，沒有合計列）＋明細（範圍內的記錄，異常列有標示）",
    description=_COMMON
    + " 條件與 GET /analytics/statistics 相同，檔案內容與其結果一致。工作表「摘要」列出開始時間、結束時間、分類、"
    "產生時間，以及每個分類一列的總計、平均、最大值、最小值（不含筆數與單位）；工作表「明細」由舊到新列出標題、數值、"
    "分類、時間戳、創建者（系統產生為「系統」）與「異常」欄，異常列另有底色。所有時間使用 start 的時區位移。不含圖表。"
    "檔案在回應前完整產生，大量資料時需數十秒，呼叫端請設定較長的逾時。",
    response_class=FileResponse,
    responses=_RESPONSES,
)
async def export_excel(params: Annotated[AnalysisQuery, Query()], _: Analyst, session: DbSession) -> FileResponse:
    export = await service.build_export(session, params)
    return FileResponse(
        export.path,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": export.content_disposition, "Cache-Control": "no-store"},
        background=BackgroundTask(service.remove_file, export.path),
    )
