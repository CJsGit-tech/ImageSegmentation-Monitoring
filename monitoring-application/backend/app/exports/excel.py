"""The workbook layout (PRD-12-R6). Synchronous and database-free: service.py feeds it.

XlsxWriter runs in constant_memory mode, so each row is flushed to a temporary file as soon as
it is written. Memory stays flat however many detail rows there are. Each sheet must therefore
be written top to bottom: the whole summary first, then the detail rows in order.

Formula injection: user text is always written with write_string(), and the workbook never
converts strings to formulas, numbers or links. Text that starts like a formula also gets the
quotePrefix cell style, so Excel shows it as text and keeps it as text after someone edits the
cell. The stored value stays the same as on the screen.

Units (PRD-12-R7, PRD-17-R7) are a display setting of the category, not part of a record, so both
sheets show the category's unit as it is set when the file is made. No unit leaves the cell blank.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

import xlsxwriter

from app.analytics.schemas import StatisticsResult
from app.records.schemas import RecordOut

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUMMARY_SHEET = "摘要"
DETAIL_SHEET = "明細"
# One header row on the detail sheet; Excel allows 1,048,576 rows per sheet.
EXCEL_MAX_ROWS = 1_048_576
MAX_DETAIL_ROWS = EXCEL_MAX_ROWS - 1

ALL_CATEGORIES = "全部分類"
ANOMALY_MARK = "異常"
SUMMARY_HEADERS = ("分類", "單位", "總計", "平均", "最大值", "最小值")
DATETIME_FORMAT = "yyyy-mm-dd hh:mm:ss"
# Cells starting with these could run as a formula if typed in (OWASP CSV injection).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
ANOMALY_FILL = "#FDE2E1"


def offset_label(zone: timezone) -> str:
    """UTC+08:00 style. Always shows the offset, also for UTC."""
    seconds = int(zone.utcoffset(None).total_seconds())
    sign = "-" if seconds < 0 else "+"
    hours, minutes = divmod(abs(seconds) // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def condition_time(value: datetime, zone: timezone) -> str:
    """A condition time as text in the file's zone, e.g. 2026-08-01 00:00:00 +08:00."""
    local = value.astimezone(zone)
    text = local.strftime("%Y-%m-%d %H:%M:%S")
    if local.microsecond:
        text += f".{local.microsecond:06d}".rstrip("0")
    return f"{text} {offset_label(zone)[3:]}"


def needs_quote_prefix(text: str) -> bool:
    return text.startswith(FORMULA_PREFIXES)


class ExcelWriter:
    """Writes the two sheets in order: write_summary once, write_details per batch, then close."""

    def __init__(self, path: str, zone: timezone, total_rows: int, tmpdir: str | None = None) -> None:
        self.zone = zone
        self.rows_written = 0
        self.workbook = xlsxwriter.Workbook(
            path,
            {
                "constant_memory": True,
                "strings_to_formulas": False,
                "strings_to_numbers": False,
                "strings_to_urls": False,
                **({"tmpdir": tmpdir} if tmpdir else {}),
            },
        )
        add = self.workbook.add_format
        self.header = add({"bold": True, "bottom": 1})
        self.label = add({"bold": True})
        self.formats: dict[tuple[str, bool], Any] = {}
        for anomaly in (False, True):
            extra = {"bg_color": ANOMALY_FILL} if anomaly else {}
            self.formats["text", anomaly] = add(extra)
            self.formats["quoted", anomaly] = add({"quote_prefix": True, **extra})
            self.formats["number", anomaly] = add(extra)
            self.formats["datetime", anomaly] = add({"num_format": DATETIME_FORMAT, **extra})
        # Both sheets are created now, so the order is 摘要, 明細 whatever is written first.
        self.units: dict[str, str] = {}
        self.summary = self.workbook.add_worksheet(SUMMARY_SHEET)
        self.detail = self.workbook.add_worksheet(DETAIL_SHEET)
        self._prepare_detail(total_rows)

    def _text(self, sheet: Any, row: int, col: int, text: str, anomaly: bool = False) -> None:
        kind = "quoted" if needs_quote_prefix(text) else "text"
        sheet.write_string(row, col, text, self.formats[kind, anomaly])

    def _number(self, sheet: Any, row: int, col: int, value: float | None, anomaly: bool = False) -> None:
        if value is None:
            sheet.write_blank(row, col, None, self.formats["number", anomaly])
        else:
            sheet.write_number(row, col, value, self.formats["number", anomaly])

    def _unit(self, sheet: Any, row: int, col: int, unit: str | None, anomaly: bool = False) -> None:
        if unit:
            self._text(sheet, row, col, unit, anomaly)
        else:
            sheet.write_blank(row, col, None, self.formats["text", anomaly])

    def write_summary(self, result: StatisticsResult, start: datetime, end: datetime, generated_at: datetime) -> None:
        """Also keeps each category's unit for the detail rows, so call it first."""
        sheet = self.summary
        sheet.set_column(0, 0, 18)
        sheet.set_column(1, 1, 10)
        sheet.set_column(2, 5, 16)
        self.units = {item.category: item.unit for item in result.categories if item.unit}
        categories = ALL_CATEGORIES if result.category is None else "、".join(result.category)
        conditions = (
            ("開始時間", condition_time(start, self.zone)),
            ("結束時間", condition_time(end, self.zone)),
            ("分類", categories),
            ("產生時間", condition_time(generated_at, self.zone)),
        )
        sheet.write_string(0, 0, "查詢條件", self.label)
        row = 1
        for label, value in conditions:
            sheet.write_string(row, 0, label, self.label)
            self._text(sheet, row, 1, value)
            row += 1
        row += 1
        for col, title in enumerate(SUMMARY_HEADERS):
            sheet.write_string(row, col, title, self.header)
        # One row per category, no total row (PRD-10-R12) and no count (PRD-10-R10).
        for item in result.categories:
            row += 1
            self._text(sheet, row, 0, item.category)
            self._unit(sheet, row, 1, item.unit)
            for col, value in enumerate((item.sum, item.avg, item.max, item.min), start=2):
                self._number(sheet, row, col, value)

    def _prepare_detail(self, total_rows: int) -> None:
        sheet = self.detail
        headers = ("標題", "數值", "單位", "分類", f"時間戳（{offset_label(self.zone)}）", "創建者", ANOMALY_MARK)
        for col, width in enumerate((32, 14, 10, 18, 22, 16, 8)):
            sheet.set_column(col, col, width)
        sheet.freeze_panes(1, 0)
        sheet.autofilter(0, 0, max(total_rows, 1), len(headers) - 1)
        self._detail_headers = headers

    def write_details(self, records: Iterable[RecordOut]) -> None:
        sheet = self.detail
        if self.rows_written == 0:
            for col, title in enumerate(self._detail_headers):
                sheet.write_string(0, col, title, self.header)
        row = self.rows_written
        for record in records:
            row += 1
            anomaly = record.is_anomaly
            self._text(sheet, row, 0, record.title, anomaly)
            sheet.write_number(row, 1, record.value, self.formats["number", anomaly])
            self._unit(sheet, row, 2, self.units.get(record.category), anomaly)
            self._text(sheet, row, 3, record.category, anomaly)
            local = record.timestamp.astimezone(self.zone).replace(tzinfo=None)
            sheet.write_datetime(row, 4, local, self.formats["datetime", anomaly])
            self._text(sheet, row, 5, record.creator_name, anomaly)
            if anomaly:
                sheet.write_string(row, 6, ANOMALY_MARK, self.formats["text", True])
        self.rows_written = row

    def close(self) -> None:
        self.workbook.close()


def file_zone(start: datetime) -> timezone:
    """The file shows every time in the offset of the requested start, like the trend does."""
    offset = start.utcoffset() or timedelta(0)
    return timezone(offset)
