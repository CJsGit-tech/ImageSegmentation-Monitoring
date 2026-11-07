"""Bulk import of records from CSV or JSON (PRD-06), in the format confirmed on LB-328.

The whole file is parsed and every row validated before anything touches the database, and the
records are written in one transaction together with the import row that claims the filename.
Either every row is saved or none is (PRD-06-R5).
"""

import csv
import hashlib
import io
import json
import logging
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.events import audit
from app.models.record_import import FILENAME_MAX_LENGTH, RecordImport
from app.models.user import User
from app.records.anomaly import CategoryRange, get_range, is_out_of_range
from app.records.import_errors import (
    ImportFileEmptyError,
    ImportFilenameInvalidError,
    ImportFilenameTakenError,
    ImportFileTooLargeError,
    ImportFileUnparseableError,
    ImportRefusedError,
    ImportRowsInvalidError,
    ImportTooManyRowsError,
    ImportUnsupportedFormatError,
)
from app.records.schemas import Category, Timestamp, Title, Value
from app.records.service import _end_read_snapshot, _utc_now, build_record

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000
MAX_REPORTED_ERRORS = 200

ImportFormat = Literal["csv", "json"]
COLUMNS = ("title", "value", "category", "timestamp")
REQUIRED_COLUMNS = ("title", "value", "category")
_EXTENSIONS: dict[str, ImportFormat] = {".csv": "csv", ".json": "json"}
_TIMESTAMP_HINT = "必須是含時區位移的 ISO-8601 時間，例如 2026-09-01T00:14:42+08:00"
# Plain decimal or exponent notation. float() alone would also take "1_000", "nan" and "inf".
_CSV_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_SHOWN_NAME_LENGTH = 100


def _reject_surrogates(value: str) -> str:
    # JSON can carry unpaired surrogates, which the database cannot store.
    if any(unicodedata.category(char) == "Cs" for char in value):
        raise ValueError("包含無效的字元")
    return value


class ImportRow(BaseModel):
    """One row, with the same field rules as the single-record API (records/schemas.py).

    Unknown keys are refused, which keeps unit and creator out (PRD-04-R10, PRD-06-R4).
    """

    model_config = ConfigDict(extra="forbid")

    title: Annotated[Title, AfterValidator(_reject_surrogates)]
    value: Value
    category: Annotated[Category, AfterValidator(_reject_surrogates)]
    timestamp: Timestamp | None = None


# --- Response shapes ---


class RowProblem(BaseModel):
    row: int = Field(description="CSV：標題列為第 1 列，第一筆資料為第 2 列（與 Excel 列號相同）；JSON：陣列中的第幾筆，從 1 起算")
    field: str | None = Field(description="title、value、category、timestamp，或檔案中不被接受的欄位名稱；整列的問題為 null")
    message: str
    type: str


class PreviousImport(BaseModel):
    imported_at: datetime = Field(description="UTC")
    row_count: int
    same_content: bool = Field(description="這次上傳的內容與當時導入的檔案完全相同")


class ImportResult(BaseModel):
    import_id: int
    filename: str
    format: ImportFormat
    imported_count: int
    anomaly_count: int = Field(description="依導入當下的範圍被判為異常的筆數（僅供參考，畫面不必顯示）")
    imported_at: datetime = Field(description="UTC；未填時間戳的列以此為時間戳")
    creator_id: int
    creator_name: str


# --- Filename (PRD-06-R7) ---


@dataclass(frozen=True)
class CheckedName:
    display: str
    key: str
    format: ImportFormat


def _shown(text: str) -> str:
    """Text from the file, made safe to echo back in a JSON response."""
    text = text.encode("utf-8", "replace").decode("utf-8")
    return text if len(text) <= _SHOWN_NAME_LENGTH else text[:_SHOWN_NAME_LENGTH] + "…"


def filename_key(base_name: str) -> str:
    """Two uploads collide when these are equal: trimmed, NFC, case-insensitive."""
    return unicodedata.normalize("NFC", base_name.strip()).casefold()


def check_filename(raw: str | None) -> CheckedName:
    # Only the base name counts; some clients send a path.
    base = re.split(r"[/\\]", raw or "")[-1].strip()
    base = unicodedata.normalize("NFC", base)
    if not base or base in {".", ".."}:
        raise ImportFilenameInvalidError(details={"reason": "blank"})
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in base):
        raise ImportFilenameInvalidError(details={"reason": "invalid_characters"})
    key = filename_key(base)
    if len(base) > FILENAME_MAX_LENGTH or len(key) > FILENAME_MAX_LENGTH:
        raise ImportFilenameInvalidError(details={"reason": "too_long", "max_length": FILENAME_MAX_LENGTH})
    extension = "." + base.rsplit(".", 1)[-1].lower() if "." in base else ""
    file_format = _EXTENSIONS.get(extension)
    if file_format is None:
        raise ImportUnsupportedFormatError(details={"filename": base, "accepted": sorted(_EXTENSIONS)})
    return CheckedName(display=base, key=key, format=file_format)


# --- Parsing (no database access; runs in a worker thread) ---


@dataclass
class ParsedFile:
    rows: list[ImportRow]


_MESSAGES = {
    "missing": "必填",
    "string_too_short": "不可空白",
    "string_type": "必須是文字",
    "string_unicode": "包含無效的字元",
    "float_type": "必須是數字",
    "float_parsing": "必須是數字",
    "finite_number": "必須是有限的數字",
    "timezone_aware": "必須含時區位移，例如 2026-09-01T00:14:42+08:00",
    "datetime_parsing": _TIMESTAMP_HINT,
    "datetime_type": _TIMESTAMP_HINT,
    "datetime_from_date_parsing": _TIMESTAMP_HINT,
    "extra_forbidden": "不是可接受的欄位（只接受 title、value、category、timestamp）",
}


def _message(error: Any) -> str:
    kind = error["type"]
    if kind == "string_too_long":
        return f"最多 {error.get('ctx', {}).get('max_length')} 個字"
    if kind == "value_error":
        return str(error["msg"]).removeprefix("Value error, ")
    return _MESSAGES.get(kind, str(error["msg"]))


def _problem(row: int, field: str | None, message: str, kind: str) -> dict[str, Any]:
    return {"row": row, "field": None if field is None else _shown(field), "message": message, "type": kind}


def _validate(row_number: int, data: dict[str, Any], problems: list[dict[str, Any]]) -> ImportRow | None:
    try:
        return ImportRow.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors():
            field = str(error["loc"][0]) if error["loc"] else None
            problems.append(_problem(row_number, field, _message(error), error["type"]))
        return None


def _decode(raw: bytes) -> str:
    try:
        # utf-8-sig drops the BOM that Excel's "CSV UTF-8" adds.
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFileUnparseableError(
            "檔案不是 UTF-8 編碼，請以 UTF-8 儲存後再上傳", details={"reason": "not_utf8", "byte_offset": exc.start}
        ) from None


def _csv_records(text: str) -> Iterator[tuple[int, list[str]]]:
    """(row number, cells) for each CSV record, counting records rather than lines, so that
    a quoted cell with a line break does not shift the numbers. The header is row 1."""
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        for number, cells in enumerate(reader, start=1):
            yield number, cells
    except csv.Error as exc:
        raise ImportFileUnparseableError(
            "CSV 格式錯誤，無法解析",
            details={"reason": "csv_malformed", "line": reader.line_num, "message": str(exc)},
        ) from None


def _is_blank(cells: list[str]) -> bool:
    return all(not cell.strip() for cell in cells)


def _csv_header(cells: list[str]) -> list[str]:
    header = [cell.strip() for cell in cells]
    duplicates = sorted({name for name in header if header.count(name) > 1})
    if duplicates:
        raise ImportFileUnparseableError(
            "標題列有重複的欄位", details={"reason": "duplicate_columns", "columns": [_shown(n) for n in duplicates]}
        )
    unknown = [name for name in header if name not in COLUMNS]
    if unknown:
        raise ImportFileUnparseableError(
            "標題列有不被接受的欄位；只接受 title、value、category、timestamp",
            details={"reason": "unknown_columns", "columns": [_shown(n) for n in unknown]},
        )
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        raise ImportFileUnparseableError(
            "標題列缺少必要欄位", details={"reason": "missing_columns", "columns": missing}
        )
    return header


def _csv_value(text: str) -> object:
    """CSV cells are text. A plain number becomes a float; anything else stays text and is
    then refused by the strict number rule, exactly like a string value in the JSON API."""
    stripped = text.strip()
    return float(stripped) if _CSV_NUMBER.fullmatch(stripped) else text


def parse_csv(text: str) -> ParsedFile:
    records = _csv_records(text)
    header: list[str] | None = None
    for _, cells in records:
        if not _is_blank(cells):
            header = _csv_header(cells)
            break
    if header is None:
        raise ImportFileEmptyError()

    rows: list[ImportRow] = []
    problems: list[dict[str, Any]] = []
    count = 0
    for number, cells in records:
        if _is_blank(cells):
            continue
        count += 1
        if count > MAX_ROWS:
            continue  # Keep counting, so the refusal can say how many there are.
        if len(cells) != len(header):
            more = len(cells) > len(header)
            message = f"欄位數（{len(cells)}）比標題列（{len(header)}）{'多' if more else '少'}"
            problems.append(_problem(number, None, message, "column_count"))
            continue
        # An empty cell is an omitted value: required fields then say so, and an empty
        # timestamp means the import time (PRD-04-R8).
        data: dict[str, Any] = {name: cell for name, cell in zip(header, cells, strict=True) if cell.strip()}
        if "value" in data:
            data["value"] = _csv_value(data["value"])
        if (row := _validate(number, data, problems)) is not None:
            rows.append(row)
    return _finish(rows, problems, count)


class _Object(dict[str, Any]):
    duplicate_keys: list[str]


def _object_pairs(pairs: list[tuple[str, Any]]) -> _Object:
    result = _Object(pairs)
    counts = Counter(key for key, _ in pairs)
    result.duplicate_keys = sorted(key for key, seen in counts.items() if seen > 1)
    return result


def _refuse_constant(name: str) -> None:
    raise ValueError(f"不接受 {name}")


def parse_json(text: str) -> ParsedFile:
    if not text.strip():
        raise ImportFileEmptyError()
    try:
        document = json.loads(text, object_pairs_hook=_object_pairs, parse_constant=_refuse_constant)
    except json.JSONDecodeError as exc:
        raise ImportFileUnparseableError(
            "JSON 格式錯誤，無法解析",
            details={"reason": "invalid_json", "line": exc.lineno, "column": exc.colno, "message": exc.msg},
        ) from None
    except (ValueError, RecursionError) as exc:
        raise ImportFileUnparseableError(
            "JSON 格式錯誤，無法解析", details={"reason": "invalid_json", "message": _shown(str(exc))}
        ) from None
    if not isinstance(document, list):
        raise ImportFileUnparseableError("JSON 最外層必須是物件陣列", details={"reason": "not_array"})
    if not document:
        raise ImportFileEmptyError()
    if len(document) > MAX_ROWS:
        return _finish([], [], len(document))

    rows: list[ImportRow] = []
    problems: list[dict[str, Any]] = []
    for number, item in enumerate(document, start=1):
        if not isinstance(item, dict):
            problems.append(_problem(number, None, "必須是物件，例如 {\"title\": …, \"value\": …, \"category\": …}", "object_type"))
            continue
        duplicates = getattr(item, "duplicate_keys", [])
        for key in duplicates:
            problems.append(_problem(number, key, "同一筆中重複出現", "duplicate_key"))
        if isinstance(item.get("value"), bool):
            # Pydantic's strict float already refuses bool; say it in the same words.
            problems.append(_problem(number, "value", _MESSAGES["float_type"], "float_type"))
            continue
        row = _validate(number, dict(item), problems)
        if row is not None and not duplicates:
            rows.append(row)
    return _finish(rows, problems, len(document))


def _finish(rows: list[ImportRow], problems: list[dict[str, Any]], count: int) -> ParsedFile:
    if count == 0:
        raise ImportFileEmptyError()
    if count > MAX_ROWS:
        raise ImportTooManyRowsError(details={"max_rows": MAX_ROWS, "row_count": count})
    if problems:
        raise ImportRowsInvalidError(
            details={
                "error_count": len(problems),
                "truncated": len(problems) > MAX_REPORTED_ERRORS,
                "errors": problems[:MAX_REPORTED_ERRORS],
            }
        )
    return ParsedFile(rows=rows)


def parse_file(raw: bytes, file_format: ImportFormat) -> ParsedFile:
    if len(raw) > MAX_FILE_BYTES:
        raise ImportFileTooLargeError(details={"max_bytes": MAX_FILE_BYTES})
    if not raw.strip():
        raise ImportFileEmptyError()
    text = _decode(raw)
    return parse_csv(text) if file_format == "csv" else parse_json(text)


# --- Writing ---


async def _refuse_if_taken(session: AsyncSession, key: str, digest: str) -> None:
    taken = (
        await session.execute(
            select(RecordImport.imported_at, RecordImport.row_count, RecordImport.content_sha256).where(
                RecordImport.filename_key == key
            )
        )
    ).first()
    if taken is not None:
        # Who imported it is left out on purpose: the rule is global (PRD-06-R7), and a name
        # clash must not tell one user what another one uploaded.
        raise ImportFilenameTakenError(
            details={
                "previous_import": PreviousImport(
                    imported_at=taken.imported_at.replace(tzinfo=UTC),
                    row_count=taken.row_count,
                    same_content=taken.content_sha256 == digest,
                ).model_dump(mode="json")
            }
        )


async def _ranges(session: AsyncSession, rows: list[ImportRow]) -> dict[str, CategoryRange | None]:
    """One lookup per spelling; the database collation decides what is the same category."""
    ranges: dict[str, CategoryRange | None] = {}
    for row in rows:
        if row.category not in ranges:
            ranges[row.category] = await get_range(session, row.category)
    return ranges


async def import_records(
    session: AsyncSession, user: User, filename: str | None, raw: bytes
) -> ImportResult:
    started = time.perf_counter()
    # A rollback below expires these objects, and an async session cannot reload them on access.
    user_id, username = user.id, user.username
    name: CheckedName | None = None
    try:
        name = check_filename(filename)
        parsed = await run_in_threadpool(parse_file, raw, name.format)
        digest = hashlib.sha256(raw).hexdigest()
        await _refuse_if_taken(session, name.key, digest)

        ranges = await _ranges(session, parsed.rows)
        now = _utc_now()
        records = [
            build_record(
                title=row.title,
                value=row.value,
                category=row.category,
                timestamp=row.timestamp or now,
                creator=user,
                # Imports never ask for a confirmation (PRD-18-R6, PRD-06-R8).
                is_anomaly=is_out_of_range(row.value, ranges[row.category]),
            )
            for row in parsed.rows
        ]
        anomaly_count = sum(record.is_anomaly for record in records)
        claim = RecordImport(
            filename=name.display,
            filename_key=name.key,
            file_format=name.format,
            row_count=len(records),
            anomaly_count=anomaly_count,
            content_sha256=digest,
            imported_by_user_id=user_id,
            imported_at=now,
        )
        await _end_read_snapshot(session)
        session.add(claim)
        session.add_all(records)
        try:
            await session.commit()
        except IntegrityError:
            # Another upload of the same name committed first; its unique key refused ours.
            await session.rollback()
            await _refuse_if_taken(session, name.key, digest)
            raise
    except ImportRefusedError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        # By id and name, not the User object: a rollback above may have expired it.
        audit(
            "records_import_rejected",
            "records import rejected",
            level=logging.WARNING,
            user_id=user_id,
            username=username,
            import_filename=name.display if name else details.get("filename"),
            file_format=name.format if name else None,
            reason=exc.reason,
            error_count=details.get("error_count"),
        )
        if name is not None and isinstance(exc.details, dict):
            exc.details = {"filename": name.display, "format": name.format, **exc.details}
        raise

    duration_ms = round((time.perf_counter() - started) * 1000)
    # Counts only: row contents never go to the log.
    audit(
        "records_imported",
        "records imported",
        user=user,
        import_id=claim.id,
        import_filename=name.display,
        file_format=name.format,
        row_count=len(records),
        anomaly_count=anomaly_count,
        duration_ms=duration_ms,
    )
    return ImportResult(
        import_id=claim.id,
        filename=name.display,
        format=name.format,
        imported_count=len(records),
        anomaly_count=anomaly_count,
        imported_at=now.replace(tzinfo=UTC),
        creator_id=user_id,
        creator_name=username,
    )
