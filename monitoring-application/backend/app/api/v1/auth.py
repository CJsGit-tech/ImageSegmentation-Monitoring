from typing import Any

from fastapi import APIRouter, status

from app.auth.dependencies import UNAUTHENTICATED_RESPONSE, CurrentUser
from app.auth.schemas import LoginRequest, LoginResponse, RegisterRequest, UserOut
from app.auth.service import login as login_user
from app.auth.service import logout as logout_user
from app.auth.service import register_user
from app.core.config import get_settings
from app.core.errors import UNAVAILABLE_RESPONSE, ErrorResponse
from app.db.session import DbSession

router = APIRouter(prefix="/auth", tags=["auth"], responses=UNAVAILABLE_RESPONSE)

_CONFLICT_RESPONSE: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse, "description": "帳號已存在，details.field 為 username"},
}
_LOGIN_FAILED_RESPONSE: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "帳號或密碼錯誤，error.code 為 INVALID_CREDENTIALS"},
}


@router.post("/register", status_code=status.HTTP_201_CREATED, summary="註冊（預設角色 Viewer）", responses=_CONFLICT_RESPONSE)
async def register(payload: RegisterRequest, session: DbSession) -> UserOut:
    return UserOut.from_user(await register_user(session, payload.username, payload.password))


@router.post("/login", summary="登入並取得 JWT；先前的登入立即失效", responses=_LOGIN_FAILED_RESPONSE)
async def login(payload: LoginRequest, session: DbSession) -> LoginResponse:
    user, token, expires_at = await login_user(session, payload.username, payload.password, get_settings())
    return LoginResponse(access_token=token, expires_at=expires_at, user=UserOut.from_user(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="登出目前的登入", responses=UNAUTHENTICATED_RESPONSE)
async def logout(user: CurrentUser, session: DbSession) -> None:
    await logout_user(session, user)


@router.get("/me", summary="目前使用者、角色與可用功能", responses=UNAUTHENTICATED_RESPONSE)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.from_user(user)
