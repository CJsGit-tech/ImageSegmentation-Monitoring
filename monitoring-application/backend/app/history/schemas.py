"""Request shape of the system-record history. The response is the shared RecordPage."""

from pydantic import Field

from app.analytics.schemas import AnalysisQuery
from app.records.schemas import CURSOR_MAX_LENGTH, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SortOrder

# Raw tracing is time-based; sorting by value or title over a narrow window of a large table
# would scan a whole index (TPM decision A for LB-336).
HISTORY_SORT = "timestamp"


class HistoryQuery(AnalysisQuery):
    """start and end are required, both inclusive, at most 3 months apart (PRD-16-R2)."""

    anomaly_only: bool = Field(default=False, description="true 時僅顯示異常記錄")
    order: SortOrder = Field(default="desc", description="依時間戳排序；desc 為新到舊（預設）")
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    cursor: str | None = Field(
        default=None, max_length=CURSOR_MAX_LENGTH, description="上一次回應的 next_cursor；須搭配相同的條件與 order"
    )
