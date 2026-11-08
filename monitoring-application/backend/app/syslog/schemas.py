"""Request and response shapes of the system log query (PRD-14). Times in responses are UTC."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.analytics.schemas import QueryTime
from app.models.system_log import Severity
from app.models.user import USERNAME_MAX_LENGTH

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
CURSOR_MAX_LENGTH = 512

EventType = Literal[
    "login_success",
    "login_failure",
    "logout",
    "login_superseded",
    "permission_denied",
    "role_changed",
    "record_created",
    "record_updated",
    "record_deleted",
    "import_result",
    "range_unit_changed",
    "system_error",
]
Username = Annotated[str, StringConstraints(strip_whitespace=True, max_length=USERNAME_MAX_LENGTH)]


class LogQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: QueryTime
    end: QueryTime
    event_type: list[EventType] | None = Field(
        default=None,
        max_length=20,
        description="事件類型，可重複，例如 ?event_type=login_failure&event_type=permission_denied；省略表示全部。選項見 GET /logs/options",
    )
    severity: list[Severity] | None = Field(
        default=None, max_length=3, description="嚴重程度 info、warning、error，可重複；省略表示全部"
    )
    username: Username | None = Field(
        default=None,
        description="執行該操作的使用者帳號，完全相符（不分大小寫與重音）；不是關鍵字搜尋。空白視為未篩選",
    )
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    cursor: str | None = Field(
        default=None, max_length=CURSOR_MAX_LENGTH, description="上一次回應的 next_cursor；須搭配完全相同的條件"
    )


class LogItem(BaseModel):
    id: int
    occurred_at: datetime = Field(description="事件發生時間，一律為 UTC")
    event_type: EventType = Field(description="事件類型（篩選用的分組）")
    event_label: str
    event_name: str = Field(description="細項事件，例如 records_import_rejected、category_unit_cleared")
    severity: Severity
    severity_label: str
    user_id: int | None = Field(description="執行操作的使用者；null 表示無（例如不存在的帳號登入失敗、系統錯誤）")
    username: str | None = Field(description="事件發生當時的帳號")
    summary: str = Field(description="簡短說明")
    details: dict[str, Any] | None = Field(description="補充欄位（編號、筆數、欄位名稱、設定值）；不含密碼、權杖或資料內容")


class LogPage(BaseModel):
    items: list[LogItem] = Field(description="由新到舊；可能為空，這時仍為 200")
    total: int = Field(description="符合條件的總筆數，不受 cursor 影響")
    limit: int
    next_cursor: str | None = Field(description="下一頁的 cursor；null 表示沒有下一頁")


class Option(BaseModel):
    value: str
    label: str


class LogOptions(BaseModel):
    event_types: list[Option] = Field(description="可篩選的事件類型，依畫面顯示順序")
    severities: list[Option]
    retention_days: float = Field(description="日誌保留天數；超過的日誌會被自動清理")
    max_range_months: int = Field(description="單次查詢時間範圍上限（月）")
