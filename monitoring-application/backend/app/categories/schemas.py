"""Request and response shapes for category ranges and units."""

from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from app.models.category_setting import UNIT_MAX_LENGTH
from app.records.anomaly import CategoryRange
from app.records.schemas import Category, Value, reject_control_characters


def _blank_means_no_unit(value: str) -> str | None:
    return reject_control_characters(value) or None


Unit = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=UNIT_MAX_LENGTH),
    AfterValidator(_blank_means_no_unit),
]


class RangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional only so that a missing bound gets the explanation below (PRD-17-R8).
    min: Value | None = Field(default=None, description="最小值（下界），必填")
    max: Value | None = Field(default=None, description="最大值（上界），必填")

    @model_validator(mode="after")
    def _both_bounds_in_order(self) -> Self:
        if self.min is None or self.max is None:
            raise PydanticCustomError("range_incomplete", "最小值與最大值都必填，不能只設定其中一個")
        if self.min >= self.max:
            raise PydanticCustomError("range_reversed", "最小值必須小於最大值")
        return self


class CategorySettingUpdate(BaseModel):
    """Omitted range or unit stays as it is."""

    model_config = ConfigDict(extra="forbid")

    # In the body, not the path: free text may hold / ? # or .. which clients rewrite in a path.
    category: Category = Field(description="自由文字，大小寫不同視為不同分類")
    range: RangeInput | None = Field(default=None, description="null 表示清除範圍；省略表示不變")
    unit: Unit | None = Field(default=None, description="自由文字，null 或空白表示清除單位；省略表示不變")

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.model_fields_set & {"range", "unit"}:
            raise ValueError("至少需要 range 或 unit 其中一項")
        return self


class CategorySettingOut(BaseModel):
    category: str
    range: CategoryRange | None
    range_status: str = Field(description="set：已設定範圍；unset：尚未設定，資料不判斷異常")
    unit: str | None
    has_data: bool = Field(description="資料中是否有此分類；模擬設備在第一筆讀數前為 false")


class CategorySettingList(BaseModel):
    items: list[CategorySettingOut] = Field(description="未設定範圍的分類排在最前")
    unset_count: int


class CategoryDisplayOut(BaseModel):
    category: str
    range: CategoryRange | None
    unit: str | None


class CategoryDisplayList(BaseModel):
    items: list[CategoryDisplayOut]
