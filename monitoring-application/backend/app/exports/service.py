"""Builds the Excel file for one analysis query (PRD-12).

The whole file is written to a temporary file before the response starts. An .xlsx is a zip,
so it cannot be streamed while it is being generated, and building it first means the empty
state, the row cap and database errors still return the normal error envelope.

Consistency: the statistics and the detail rows are read in the same session and transaction,
so InnoDB's REPEATABLE READ snapshot gives both the same records even while the simulator
writes. The detail row count is still checked against the statistics before the file is served.
"""

import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import service as analytics
from app.analytics.schemas import AnalysisQuery
from app.core.errors import AppError
from app.exports import excel
from app.records.schemas import RecordOut

logger = logging.getLogger(__name__)

# Records fetched per database round trip, and written per worker-thread call.
BATCH_SIZE = 5000
# Read at call time, so tests can lower it.
MAX_DETAIL_ROWS = excel.MAX_DETAIL_ROWS
FILENAME_STEM = "資料分析"
ASCII_FILENAME_STEM = "analysis"


class ExportNoDataError(AppError):
    status_code = 404
    code = "EXPORT_NO_DATA"
    message = "條件內沒有資料，沒有可下載的內容。"


class ExportTooLargeError(AppError):
    status_code = 422
    code = "EXPORT_TOO_LARGE"
    message = "明細超過 Excel 單一工作表上限（1,048,575 筆），請縮短時間範圍或減少分類後分批下載。"


class ExportInconsistentError(AppError):
    status_code = 500
    code = "EXPORT_INCONSISTENT"
    message = "匯出時資料筆數與統計不一致，請重試。"


@dataclass(frozen=True)
class ExportFile:
    path: str
    filename: str
    ascii_filename: str
    rows: int
    size: int

    @property
    def content_disposition(self) -> str:
        """RFC 6266 with an ASCII fallback and the UTF-8 name as filename* (RFC 5987)."""
        return f"attachment; filename=\"{self.ascii_filename}\"; filename*=UTF-8''{quote(self.filename, safe='')}"


def _conditions(query: AnalysisQuery, categories: list[str] | None) -> dict[str, object]:
    return {"start": query.start.isoformat(), "end": query.end.isoformat(), "category": categories}


async def build_export(
    session: AsyncSession,
    query: AnalysisQuery,
    *,
    now: datetime | None = None,
    tmpdir: str | None = None,
) -> ExportFile:
    """Raises the analysis errors (VALIDATION_ERROR, RANGE_TOO_LONG), EXPORT_NO_DATA or
    EXPORT_TOO_LARGE before writing anything. The caller deletes the returned file."""
    began = time.perf_counter()
    window = analytics.check_query(query)
    result = await analytics.category_statistics(session, query)
    if not result.has_data:
        raise ExportNoDataError(details=_conditions(query, window.categories))
    total = sum(item.count for item in result.categories)
    if total > MAX_DETAIL_ROWS:
        raise ExportTooLargeError(details={"rows": total, "max_rows": MAX_DETAIL_ROWS})

    zone = excel.file_zone(query.start)
    # Whole seconds, so the sheet and the file name show the same generation time.
    generated_at = (now or datetime.now(UTC)).astimezone(zone).replace(microsecond=0)
    handle, path = tempfile.mkstemp(prefix="analysis-export-", suffix=".xlsx", dir=tmpdir)
    os.close(handle)
    try:
        writer = excel.ExcelWriter(path, zone, total, tmpdir=tmpdir)
        writer.write_summary(result, query.start, query.end, generated_at)
        batch: list[RecordOut] = []
        async for record in analytics.iter_records(session, query, batch_size=BATCH_SIZE):
            batch.append(record)
            if len(batch) >= BATCH_SIZE:
                # Writing is CPU work; a worker thread keeps the event loop serving others.
                await to_thread.run_sync(writer.write_details, batch)
                batch = []
        if batch:
            await to_thread.run_sync(writer.write_details, batch)
        await to_thread.run_sync(writer.close)
        if writer.rows_written != total:
            raise ExportInconsistentError(details={"rows": writer.rows_written, "expected": total})
    except BaseException:
        os.unlink(path)
        raise

    stamp = generated_at.strftime("%Y%m%d-%H%M%S")
    export = ExportFile(
        path=path,
        filename=f"{FILENAME_STEM}_{stamp}.xlsx",
        ascii_filename=f"{ASCII_FILENAME_STEM}_{stamp}.xlsx",
        rows=total,
        size=os.path.getsize(path),
    )
    logger.info(
        "excel export built",
        extra={
            "rows": total,
            "categories": len(result.categories),
            "bytes": export.size,
            "seconds": round(time.perf_counter() - began, 3),
        },
    )
    return export


def remove_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
