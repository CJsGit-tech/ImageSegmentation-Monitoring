"""Bulk import endpoint (PRD-06). Format and rules: app.records.importing."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from starlette.datastructures import UploadFile
from starlette.types import Message

from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.events import audit
from app.auth.permissions import Capability
from app.core.errors import UNAVAILABLE_RESPONSE, ErrorResponse
from app.db.session import DbSession
from app.models.user import User
from app.records import importing
from app.records.import_errors import ImportFileMissingError, ImportFileTooLargeError, ImportRefusedError
from app.records.importing import MAX_FILE_BYTES, MAX_REPORTED_ERRORS, MAX_ROWS, ImportResult

# Room for the multipart boundaries and part headers around the file itself.
_MULTIPART_OVERHEAD = 64 * 1024
_REQUEST_LIMIT = MAX_FILE_BYTES + _MULTIPART_OVERHEAD

Importer = Annotated[User, Depends(require_capability(Capability.RECORDS_IMPORT))]

_DESCRIPTION = f"""
以 `multipart/form-data` 的 `file` 欄位上傳一個 `.csv` 或 `.json` 檔（依副檔名判斷，不分大小寫）。

- **欄位：** `title`（必填）、`value`（必填，數字；JSON 必須是數字型別）、`category`（必填）、
  `timestamp`（選填，含時區位移的 ISO-8601，例如 `2026-09-01T00:14:42+08:00`；省略、空白或 null 時為導入當下）。
  驗證規則與單筆新增相同。單位與創建者不從檔案讀取，創建者一律為目前使用者。
- **CSV：** UTF-8（可帶 BOM），第一列為標題列；欄位順序不限，`timestamp` 欄可省略；其他欄位整個檔案拒絕。整列空白的列略過。
- **JSON：** 最外層為物件陣列，每個物件只能有上述四個鍵。
- **整批：** 任何一列有問題就整批拒絕、一筆都不寫入，回報列號與原因（最多 {MAX_REPORTED_ERRORS} 筆，
  `error_count` 為總數）。超出範圍的列直接存為異常，不需要也不接受確認。
- **檔名：** 同一檔名（只看檔名本身，去除前後空白、不分大小寫）已成功導入過就拒絕；被拒絕的上傳不佔用檔名。
- **上限：** 檔案 {MAX_FILE_BYTES // (1024 * 1024)} MB、{MAX_ROWS} 筆資料列。
"""

_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {
        "model": ErrorResponse,
        "description": "IMPORT_FILENAME_TAKEN：相同檔名已成功導入過；details.previous_import 含 imported_at、row_count、same_content",
    },
    413: {"model": ErrorResponse, "description": "IMPORT_FILE_TOO_LARGE：超過檔案大小上限；details.max_bytes"},
    415: {"model": ErrorResponse, "description": "IMPORT_UNSUPPORTED_FORMAT：副檔名不是 .csv 或 .json"},
    422: {
        "model": ErrorResponse,
        "description": (
            "IMPORT_ROWS_INVALID（details.errors：row、field、message、type；error_count；truncated）、"
            "IMPORT_FILE_UNPARSEABLE（details.reason：not_utf8、invalid_json、not_array、csv_malformed、"
            "missing_columns、unknown_columns、duplicate_columns）、IMPORT_FILE_EMPTY、IMPORT_TOO_MANY_ROWS、"
            "IMPORT_FILENAME_INVALID、IMPORT_FILE_MISSING。以上都不會寫入任何資料"
        ),
    },
}

_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary", "description": ".csv 或 .json"}},
                }
            }
        },
    }
}

router = APIRouter(
    prefix="/records", tags=["records"], responses={**UNAVAILABLE_RESPONSE, **UNAUTHENTICATED_RESPONSE}
)


def _refuse(user: User, exc: ImportRefusedError) -> ImportRefusedError:
    audit(
        "records_import_rejected",
        "records import rejected",
        user=user,
        level=logging.WARNING,
        import_filename=None,
        file_format=None,
        reason=exc.reason,
        error_count=None,
    )
    return exc


def _limited(request: Request, user: User) -> Request:
    """The same request, refusing the body once it passes the size limit, so that an
    oversized upload is never read, or spooled to disk, in full."""
    received = 0

    async def receive() -> Message:
        nonlocal received
        message = await request.receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > _REQUEST_LIMIT:
                raise _refuse(user, ImportFileTooLargeError(details={"max_bytes": MAX_FILE_BYTES}))
        return message

    return Request(request.scope, receive)


@router.post(
    "/imports",
    status_code=status.HTTP_201_CREATED,
    summary="批量導入 CSV／JSON（整批全部寫入或完全不寫入）",
    description=_DESCRIPTION,
    responses={**FORBIDDEN_RESPONSE, **_RESPONSES},
    openapi_extra=_REQUEST_BODY,
)
async def import_records(request: Request, user: Importer, session: DbSession) -> ImportResult:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _REQUEST_LIMIT:
        raise _refuse(user, ImportFileTooLargeError(details={"max_bytes": MAX_FILE_BYTES}))
    async with _limited(request, user).form(max_files=1, max_fields=20) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise _refuse(user, ImportFileMissingError())
        raw = await upload.read(MAX_FILE_BYTES + 1)
        filename = upload.filename
    return await importing.import_records(session, user, filename, raw)
