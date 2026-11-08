"""Shapes of the real-time contract. Every time is UTC with an offset. The REST live window and
the WebSocket snapshot message share LiveSnapshot, so a client can use either."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, BeforeValidator, Field

from app.core.errors import ErrorBody
from app.records.anomaly import CategoryRange
from app.records.schemas import _require_iso_text

RangeStatus = Literal["set", "unset"]


class Point(BaseModel):
    value: float
    timestamp: datetime
    is_anomaly: bool


class GeneratorState(BaseModel):
    running: bool = Field(description="此後端目前正在產生模擬資料")
    last_tick_at: datetime | None = Field(description="最近一次產生資料的時間；尚未產生為 null")


class LiveSummary(BaseModel):
    anomalous_category_count: int = Field(description="最新一筆讀數為異常的分類數")
    category_count: int
    anomaly_count_5m: int = Field(description="最近 5 分鐘的異常筆數")
    last_reading_at: datetime | None = Field(description="最後收到資料的時間；尚無資料為 null")
    stalled: bool = Field(
        description="server_time 與 last_reading_at 相差超過 stale_after_seconds：資料停止到達（已暫停更新）"
    )


class LiveCategory(BaseModel):
    category: str
    title: str
    unit: str | None
    range: CategoryRange | None = Field(description="目前的範圍；清除後為 null（不判斷異常）")
    range_status: RangeStatus
    latest: Point | None
    anomaly_count_5m: int
    readings: list[Point] = Field(description="最近 5 分鐘的讀數，由舊到新")


class AnomalyItem(BaseModel):
    category: str
    title: str
    value: float
    unit: str | None
    range: CategoryRange | None = Field(description="判定時的範圍；啟動前已保存的異常則為目前的範圍")
    timestamp: datetime


class LiveSnapshot(BaseModel):
    server_time: datetime
    interval_seconds: float = Field(description="每個設備產生一筆資料的間隔（PRD-07-R1）")
    window_seconds: int = Field(description="即時窗口長度（5 分鐘）")
    stale_after_seconds: float = Field(description="超過此秒數沒有新資料即視為已暫停更新：2 × interval + 10")
    generator: GeneratorState
    summary: LiveSummary
    categories: list[LiveCategory] = Field(description="4 個模擬設備的分類，順序固定")
    recent_anomalies: list[AnomalyItem] = Field(description="最近的異常，最新在前，最多 20 筆")


class LiveReading(BaseModel):
    category: str
    title: str
    value: float
    unit: str | None
    range: CategoryRange | None = Field(description="判定此筆時的範圍")
    is_anomaly: bool
    timestamp: datetime = Field(description="產生當下的時間")


# --- WebSocket messages (server to client) ---


class SnapshotMessage(LiveSnapshot):
    type: Literal["snapshot"] = "snapshot"


class ReadingsMessage(BaseModel):
    type: Literal["readings"] = "readings"
    server_time: datetime
    tick_at: datetime
    simulated: Literal[True] = True
    readings: list[LiveReading]
    summary: LiveSummary
    category_anomaly_counts_5m: dict[str, int]


class HeartbeatMessage(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    server_time: datetime
    last_reading_at: datetime | None
    generator: GeneratorState


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    error: ErrorBody


# --- Custom time range ---

RangeTime = Annotated[
    AwareDatetime,
    BeforeValidator(_require_iso_text),
    Field(description="含時區位移的 ISO-8601，例如 2026-11-05T08:00:00+08:00"),
]


class RangeParams(BaseModel):
    start: RangeTime
    end: RangeTime


class Aggregation(BaseModel):
    bucket_seconds: int | None = Field(description="彙總區間秒數；null 表示未彙總（逐筆）")
    label: str = Field(description="例如「每 1 分鐘」；未彙總為「逐筆」")


class Bucket(BaseModel):
    bucket_start: datetime = Field(description="區間開始；逐筆時為該筆時間")
    avg: float
    min: float
    max: float
    count: int
    anomaly_count: int = Field(description="區間內的異常筆數；大於 0 時在 min 或 max 中越界的一端標示異常")


class RangeCategory(BaseModel):
    category: str
    title: str
    unit: str | None
    range: CategoryRange | None
    range_status: RangeStatus
    latest: Point | None = Field(description="範圍內最後一筆")
    anomaly_count: int
    points: list[Bucket]


class RangeSummary(BaseModel):
    anomalous_category_count: int = Field(description="範圍內曾有異常的分類數")
    category_count: int
    anomaly_count: int
    last_reading_at: datetime | None = Field(description="範圍內最後一筆資料的時間")


class RangeView(BaseModel):
    start: datetime
    end: datetime
    aggregation: Aggregation
    summary: RangeSummary
    categories: list[RangeCategory]
    recent_anomalies: list[AnomalyItem] = Field(description="範圍內最近的異常，最新在前，最多 20 筆")
