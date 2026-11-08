"""Response shape of GET /db-status (PRD-15). Times are naive UTC, like every other response."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DbState = Literal["available", "unavailable"]
ErrorCategory = Literal["timeout", "unreachable", "auth_failed", "pool_exhausted", "error"]
WarningCode = Literal["SLOW_RESPONSE", "POOL_HIGH", "BATCH_STALE"]


class DbError(BaseModel):
    category: ErrorCategory = Field(
        description=(
            "timeout：資料庫在時限內沒有回應；unreachable：無法連線；auth_failed：資料庫拒絕登入；"
            "pool_exhausted：連線池已滿；error：其他資料庫錯誤"
        )
    )
    message: str = Field(description="依 category 固定的中文說明；不含資料庫原始錯誤文字")


class PoolUsage(BaseModel):
    in_use: int = Field(description="已借出的連線數（含模擬器常駐的一條鎖定連線）")
    idle: int = Field(description="已開啟但閒置的連線數")
    open: int = Field(description="已開啟的連線總數（in_use + idle）")
    capacity: int = Field(description="連線池上限（DB_POOL_SIZE + DB_MAX_OVERFLOW）")
    usage_ratio: float = Field(description="in_use / capacity，0 到 1")


class DbWarning(BaseModel):
    code: WarningCode = Field(
        description=(
            "SLOW_RESPONSE：回應時間 ≥ 250 ms；POOL_HIGH：連線池使用 ≥ 80%；"
            "BATCH_STALE：最後一次批次寫入早於 3 個模擬器間隔。門檻為工程選擇"
        )
    )
    message: str


class DbStatus(BaseModel):
    status: DbState = Field(description="available：資料庫可用；unavailable：本次檢查失敗")
    checked_at: datetime = Field(description="本次檢查時間（UTC），畫面的「最後更新時間」")
    last_available_at: datetime | None = Field(
        description="此後端程序最後一次檢查成功的時間（UTC）；重新啟動後尚未成功過則為 null"
    )
    error: DbError | None = Field(description="status 為 unavailable 時的原因；可用時為 null")
    response_time_ms: float | None = Field(
        description="一次最簡單查詢的來回時間（毫秒），不含建立連線；不可用時為 null"
    )
    pool: PoolUsage = Field(description="後端連線池使用狀況；資料庫不可用時仍為後端本身的數字")
    record_count: int | None = Field(
        description="所有來源（手動、導入、系統產生）的資料總筆數，精確值，最多快取 60 秒；不可用或尚未算出時為 null"
    )
    record_count_as_of: datetime | None = Field(description="record_count 算出的時間（UTC）")
    last_batch_write_at: datetime | None = Field(
        description=(
            "最後一次批次寫入時間（UTC）：模擬器最近一次寫入的系統產生資料的時間戳；"
            "模擬器在產生後 5 秒內批次寫入。不可用或尚無系統資料時為 null"
        )
    )
    warnings: list[DbWarning] = Field(description="超過門檻的指標；可為空。主狀態仍只有可用／不可用")
    refresh_interval_seconds: int = Field(description="畫面自動更新間隔（PRD-15-R5）")
