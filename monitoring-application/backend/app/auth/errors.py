"""Authentication failures. Clients branch on these stable error.code values."""

from typing import Any

from app.core.errors import AppError, UnauthorizedError


class NotAuthenticatedError(UnauthorizedError):
    """No usable token: missing, malformed, wrong signature or unknown user."""

    headers = {"WWW-Authenticate": "Bearer"}


class TokenExpiredError(NotAuthenticatedError):
    code = "TOKEN_EXPIRED"
    message = "登入已過期，請重新登入"


class SessionSupersededError(NotAuthenticatedError):
    code = "SESSION_SUPERSEDED"
    message = "此帳號已在其他地方登入，此處的登入已失效，請重新登入"


class SessionLoggedOutError(NotAuthenticatedError):
    code = "SESSION_LOGGED_OUT"
    message = "此次登入已登出，請重新登入"


class InvalidCredentialsError(NotAuthenticatedError):
    code = "INVALID_CREDENTIALS"
    message = "帳號或密碼錯誤"


class FieldValidationError(AppError):
    """Same shape as request validation failures: details is a list of {field, message, type}."""

    status_code = 422
    code = "VALIDATION_ERROR"
    message = "請求資料驗證失敗"

    def __init__(self, details: list[dict[str, Any]]) -> None:
        super().__init__(details=details)
