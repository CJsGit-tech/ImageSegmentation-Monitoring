"""Unified error contract: every non-2xx response has the ErrorResponse shape."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger(__name__)

_HTTP_DEFAULTS: dict[int, tuple[str, str]] = {
    400: ("BAD_REQUEST", "請求格式不正確"),
    401: ("UNAUTHORIZED", "尚未登入或登入已失效"),
    403: ("FORBIDDEN", "沒有執行此操作的權限"),
    404: ("NOT_FOUND", "找不到指定的資源"),
    405: ("METHOD_NOT_ALLOWED", "不支援此請求方法"),
    409: ("CONFLICT", "資料狀態衝突"),
    422: ("VALIDATION_ERROR", "請求資料驗證失敗"),
    500: ("INTERNAL_ERROR", "伺服器發生內部錯誤"),
    503: ("DATABASE_UNAVAILABLE", "資料庫目前無法使用"),
}


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AppError(Exception):
    """Base class for expected errors. Raise it or a subclass from any handler."""

    status_code = 500
    code = "INTERNAL_ERROR"
    message = _HTTP_DEFAULTS[500][1]
    headers: dict[str, str] | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        self.headers = headers or self.headers
        super().__init__(self.message)


class UnauthorizedError(AppError):
    status_code, (code, message) = 401, _HTTP_DEFAULTS[401]


class ForbiddenError(AppError):
    status_code, (code, message) = 403, _HTTP_DEFAULTS[403]


class NotFoundError(AppError):
    status_code, (code, message) = 404, _HTTP_DEFAULTS[404]


class ConflictError(AppError):
    status_code, (code, message) = 409, _HTTP_DEFAULTS[409]


def _response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details, request_id=request_id_var.get())
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


def internal_error_response() -> JSONResponse:
    return _response(500, *_HTTP_DEFAULTS[500])


async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return _response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    default = _HTTP_DEFAULTS.get(exc.status_code)
    code, message = default if default else (f"HTTP_{exc.status_code}", str(exc.detail))
    return _response(exc.status_code, code, message, headers=dict(exc.headers) if exc.headers else None)


async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Input values are left out on purpose, since they may hold passwords.
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return _response(422, *_HTTP_DEFAULTS[422], details=details)


async def _handle_database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("database error", exc_info=exc)
    if isinstance(exc, (OperationalError, InterfaceError, PoolTimeoutError)):
        return _response(503, *_HTTP_DEFAULTS[503])
    return internal_error_response()


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(SQLAlchemyError, _handle_database_error)


# Pass as responses= on a router or route so that Swagger shows the real error shape.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "請求資料驗證失敗"},
    500: {"model": ErrorResponse, "description": "伺服器內部錯誤"},
}
UNAVAILABLE_RESPONSE: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorResponse, "description": "資料庫無法使用或結構不是最新"},
}
