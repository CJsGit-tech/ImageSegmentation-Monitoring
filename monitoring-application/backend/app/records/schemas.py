"""Request and response shapes. Timestamps are naive UTC inside the application."""

import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from app.models.record import CATEGORY_MAX_LENGTH, TITLE_MAX_LENGTH, Record
from app.records.anomaly import CategoryRange

SYSTEM_CREATOR_NAME = "系統"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
CURSOR_MAX_LENGTH = 2048

SortField = Literal["title", "value", "category", "timestamp"]
SortOrder = Literal["asc", "desc"]

# Pydantic would otherwise read bare numbers as Unix time.
_TIMESTAMP_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}[Tt ]")


def reject_control_characters(value: str) -> str:
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("不可包含控制字元")
    return value


def _require_iso_text(value: object) -> object:
    if not isinstance(value, str) or not _TIMESTAMP_SHAPE.match(value):
        raise ValueError("必須是含時區位移的 ISO-8601 時間，例如 2026-09-01T00:14:42+08:00")
    return value


def _to_naive_utc(value: datetime) -> datetime:
    try:
        return value.astimezone(UTC).replace(tzinfo=None)
    except OverflowError:
        raise ValueError("時間超出可表示的範圍") from None


Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH),
    AfterValidator(reject_control_characters),
]
Category = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=CATEGORY_MAX_LENGTH),
    AfterValidator(reject_control_characters),
]
# Strict: a number written as a string is rejected rather than converted (PRD-04-R2).
Value = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Timestamp = Annotated[
    AwareDatetime,
    BeforeValidator(_require_iso_text),
    AfterValidator(_to_naive_utc),
    Field(description="含時區位移的 ISO-8601，例如 2026-09-01T00:14:42+08:00"),
]


_CONFIRM_DESCRIPTION = "確認儲存後將成為異常。判斷為異常卻未確認時回 409，不寫入；判斷為正常時此欄位無作用"


class RecordCreate(BaseModel):
    # Unknown fields are refused, which is what keeps unit and creator out (PRD-04-R10).
    model_config = ConfigDict(extra="forbid")

    title: Title
    value: Value
    category: Category = Field(description="自由文字；大小寫不同視為不同分類")
    timestamp: Timestamp | None = Field(default=None, description="含時區位移的 ISO-8601；省略或 null 時為建立當下")
    confirm_anomaly: StrictBool = Field(default=False, description=_CONFIRM_DESCRIPTION)
    client_request_id: UUID | None = Field(
        default=None,
        description=(
            "同一次新增的重試請帶相同的 UUID：若已寫入，回傳既有記錄而不新增第二筆。"
            "同一使用者以相同 UUID 送出不同內容時回 409（CLIENT_REQUEST_REUSED）"
        ),
    )


class RecordUpdate(BaseModel):
    """Omitted fields stay as they are. None of the fields can be cleared with null."""

    model_config = ConfigDict(extra="forbid")

    title: Title = None  # type: ignore[assignment]
    value: Value = None  # type: ignore[assignment]
    category: Category = None  # type: ignore[assignment]
    timestamp: Timestamp = None  # type: ignore[assignment]
    confirm_anomaly: StrictBool = Field(default=False, description=_CONFIRM_DESCRIPTION)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if not self.model_fields_set - {"confirm_anomaly"}:
            raise ValueError("至少需要一個要修改的欄位")
        return self


class RecordListParams(BaseModel):
    # Unknown parameters are refused, so a filter that does not exist yet cannot be
    # mistaken for one that returned everything.
    model_config = ConfigDict(extra="forbid")

    sort_by: SortField = "timestamp"
    order: SortOrder = "desc"
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    cursor: str | None = Field(
        default=None, max_length=CURSOR_MAX_LENGTH, description="上一次回應的 next_cursor；須搭配相同的 sort_by 與 order"
    )
    title: str | None = Field(default=None, max_length=TITLE_MAX_LENGTH, description="標題包含此文字，不分大小寫；空白視為未篩選")
    category: str | None = Field(default=None, max_length=CATEGORY_MAX_LENGTH, description="分類完全相符；空白視為未篩選")
    value_min: float | None = Field(default=None, allow_inf_nan=False, description="數值下限（含）")
    value_max: float | None = Field(default=None, allow_inf_nan=False, description="數值上限（含）")
    time_from: Timestamp | None = Field(default=None, description="時間下限（含），須含時區位移")
    time_to: Timestamp | None = Field(default=None, description="時間上限（含），須含時區位移")
    anomaly_only: bool = Field(default=False, description="true 時僅顯示異常記錄")


class AnomalyCheckParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    value: float = Field(allow_inf_nan=False)


class AnomalyCheck(BaseModel):
    category: str
    value: float
    would_be_anomaly: bool = Field(description="依目前範圍的判斷。最終結果以儲存時為準")
    range: CategoryRange | None = Field(description="null 表示尚未設定範圍")
    unit: str | None


class RecordOut(BaseModel):
    id: int
    title: str
    value: float
    category: str
    timestamp: datetime = Field(description="一律為 UTC")
    creator_id: int | None = Field(description="系統產生的記錄為 null")
    creator_name: str = Field(description=f"使用者名稱；系統產生的記錄為「{SYSTEM_CREATOR_NAME}」")
    is_system: bool = Field(description="請以此判斷系統產生的記錄，不要比對 creator_name")
    is_anomaly: bool

    @classmethod
    def from_record(cls, record: Record, creator_name: str | None) -> "RecordOut":
        return cls(
            id=record.id,
            title=record.title,
            value=record.value,
            category=record.category,
            timestamp=record.timestamp.replace(tzinfo=UTC),
            creator_id=record.created_by_user_id,
            creator_name=SYSTEM_CREATOR_NAME if record.created_by_user_id is None else creator_name or "",
            is_system=record.created_by_user_id is None,
            is_anomaly=record.is_anomaly,
        )


class RecordPage(BaseModel):
    items: list[RecordOut] = Field(description="可能為空：沒有符合條件的資料，或此 cursor 之後的記錄已被刪除；這時仍為 200")
    total: int = Field(description="符合篩選條件的總筆數，不受 cursor 影響")
    limit: int
    next_cursor: str | None = Field(description="下一頁的 cursor；null 表示沒有下一頁")


class CategoryList(BaseModel):
    items: list[str]
