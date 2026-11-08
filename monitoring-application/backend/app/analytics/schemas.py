"""Request and response shapes of the analysis contract. Times in responses are UTC with an
offset, except trend bucket starts, which carry the request's offset."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from app.models.record import CATEGORY_MAX_LENGTH
from app.records.anomaly import CategoryRange
from app.records.schemas import (
    CURSOR_MAX_LENGTH,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SortField,
    SortOrder,
    _require_iso_text,
)

MAX_CATEGORIES = 50

Granularity = Literal["minute", "hour", "day"]
GRANULARITY_SECONDS: dict[str, int] = {"minute": 60, "hour": 3600, "day": 86400}
GRANULARITY_LABELS: dict[str, str] = {"minute": "每分鐘", "hour": "每小時", "day": "每天"}
DEFAULT_GRANULARITY: Granularity = "hour"

def _iso_text_or_datetime(value: object) -> object:
    """Query strings must be ISO-8601 text; Python callers (LB-332) may pass aware datetimes."""
    return value if isinstance(value, datetime) else _require_iso_text(value)


QueryTime = Annotated[
    AwareDatetime,
    BeforeValidator(_iso_text_or_datetime),
    Field(description="含時區位移的 ISO-8601，例如 2026-08-01T00:00:00+08:00；包含此時間"),
]
# Stripped like saved categories are, so a requested name matches the stored spelling exactly.
CategoryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CATEGORY_MAX_LENGTH)]


class AnalysisQuery(BaseModel):
    """The conditions shared by statistics, trend, the anomaly list and the Excel export."""

    model_config = ConfigDict(extra="forbid")

    start: QueryTime
    end: QueryTime
    category: list[CategoryName] | None = Field(
        default=None,
        max_length=MAX_CATEGORIES,
        description="可重複，例如 ?category=溫度&category=壓力；完全相符、大小寫視為不同分類。省略表示全部分類",
    )


class TrendQuery(AnalysisQuery):
    granularity: Granularity = Field(default=DEFAULT_GRANULARITY, description="彙總粒度，僅影響趨勢")


class AnomalyListQuery(AnalysisQuery):
    sort_by: SortField = "timestamp"
    order: SortOrder = "desc"
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    cursor: str | None = Field(
        default=None, max_length=CURSOR_MAX_LENGTH, description="上一次回應的 next_cursor；須搭配相同的條件、sort_by 與 order"
    )


# --- Statistics (PRD-10) ---


class CategoryStatistics(BaseModel):
    category: str
    unit: str | None = Field(description="分類設定目前的單位；未設定為 null（只顯示數字）")
    count: int = Field(
        description="範圍內的記錄數。僅供人工核對（各分類 count 加總 = 記錄列表同條件的 total），畫面不顯示（PRD-10-R10）"
    )
    sum: float | None = Field(description="總計；此分類在範圍內沒有資料時為 null")
    avg: float | None = Field(description="平均")
    max: float | None = Field(description="最大值")
    min: float | None = Field(description="最小值")


class StatisticsResult(BaseModel):
    start: datetime
    end: datetime
    category: list[str] | None = Field(description="查詢的分類；null 表示全部分類")
    has_data: bool = Field(description="false 時顯示空狀態說明，而不是 0（PRD-10-R6）")
    categories: list[CategoryStatistics] = Field(
        description=(
            "每個分類一列，沒有跨分類合計列（PRD-10-R12）。未指定分類時只列範圍內有資料的分類（依分類排序）；"
            "指定分類時每個指定的分類一列（依指定順序），沒有資料的分類 count 為 0、統計值為 null"
        )
    )


# --- Trend (PRD-11) ---


class TrendPoint(BaseModel):
    bucket_start: datetime = Field(description="區間開始，使用請求 start 的時區位移")
    avg: float = Field(description="區間內數值的平均（PRD-11-R7）")
    min: float
    max: float
    count: int
    anomaly_count: int = Field(description="區間內的異常筆數；大於 0 時圖上標示異常（PRD-11-R9）")
    anomaly_min: float | None = Field(description="區間內異常記錄的最小數值；沒有異常為 null")
    anomaly_max: float | None = Field(description="區間內異常記錄的最大數值；沒有異常為 null")


class TrendSeries(BaseModel):
    category: str
    unit: str | None
    range: CategoryRange | None = Field(description="目前的範圍，畫最小與最大值線用；未設定為 null")
    range_status: Literal["set", "unset"]
    points: list[TrendPoint] = Field(description="由舊到新；沒有資料的區間不列出")


class TrendResult(BaseModel):
    start: datetime
    end: datetime
    category: list[str] | None
    granularity: Granularity
    bucket_seconds: int
    label: str
    utc_offset: str = Field(description="區間對齊的時區位移（取自 start），例如 +08:00")
    max_points_per_category: int
    has_data: bool
    categories: list[TrendSeries] = Field(description="每個分類一格小圖；列出規則同統計")
