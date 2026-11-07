"""Bulk import refusals (PRD-06). Every one of them means nothing was written.

Clients branch on error.code; `reason` is the short value written to the audit log.
"""

from app.core.errors import AppError


class ImportRefusedError(AppError):
    reason = "refused"


class ImportRowsInvalidError(ImportRefusedError):
    status_code = 422
    code = "IMPORT_ROWS_INVALID"
    message = "檔案有問題，這個檔案的資料一筆都沒有寫入；修正後可用原檔名重新上傳"
    reason = "rows_invalid"


class ImportFilenameTakenError(ImportRefusedError):
    status_code = 409
    code = "IMPORT_FILENAME_TAKEN"
    message = "相同檔名已成功導入過，請改檔名後再上傳"
    reason = "filename_taken"


class ImportUnsupportedFormatError(ImportRefusedError):
    status_code = 415
    code = "IMPORT_UNSUPPORTED_FORMAT"
    message = "只接受 .csv 或 .json 檔案"
    reason = "unsupported_format"


class ImportFileUnparseableError(ImportRefusedError):
    status_code = 422
    code = "IMPORT_FILE_UNPARSEABLE"
    message = "無法解析檔案內容，整個檔案未導入"
    reason = "unparseable"


class ImportFileEmptyError(ImportRefusedError):
    status_code = 422
    code = "IMPORT_FILE_EMPTY"
    message = "檔案沒有任何資料列，未導入"
    reason = "empty"


class ImportFileTooLargeError(ImportRefusedError):
    status_code = 413
    code = "IMPORT_FILE_TOO_LARGE"
    message = "檔案太大，未導入"
    reason = "too_large"


class ImportTooManyRowsError(ImportRefusedError):
    status_code = 422
    code = "IMPORT_TOO_MANY_ROWS"
    message = "資料列太多，未導入；請分成多個檔案"
    reason = "too_many_rows"


class ImportFilenameInvalidError(ImportRefusedError):
    status_code = 422
    code = "IMPORT_FILENAME_INVALID"
    message = "檔名無效，請確認檔名後再上傳"
    reason = "filename_invalid"


class ImportFileMissingError(ImportRefusedError):
    status_code = 422
    code = "IMPORT_FILE_MISSING"
    message = "請以 multipart/form-data 的 file 欄位上傳一個檔案"
    reason = "file_missing"
