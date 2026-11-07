"""Record failures. Clients branch on these stable error.code values."""

from app.core.errors import ConflictError, ForbiddenError, NotFoundError


class RecordNotFoundError(NotFoundError):
    code = "RECORD_NOT_FOUND"
    message = "這筆記錄已不存在"


class NotRecordOwnerError(ForbiddenError):
    code = "NOT_RECORD_OWNER"
    message = "只有建立者或 Admin 可修改或刪除這筆記錄"


class AnomalyRecordReadOnlyError(ForbiddenError):
    code = "ANOMALY_RECORD_READ_ONLY"
    message = "異常記錄已封存，任何人都不能修改或刪除"


class AnomalyConfirmationRequiredError(ConflictError):
    code = "ANOMALY_CONFIRMATION_REQUIRED"
    message = "儲存後將標為異常，且之後任何人都不能修改或刪除；請確認後再送出"


class ClientRequestReusedError(ConflictError):
    code = "CLIENT_REQUEST_REUSED"
    message = "這次送出已處理過，但內容與現有記錄不同；請到資料列表查看"
