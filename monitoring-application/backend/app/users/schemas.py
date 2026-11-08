"""Request and response shapes for user management. Times are naive UTC inside the application."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.user import USERNAME_MAX_LENGTH, Role, User

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
CURSOR_MAX_LENGTH = 256


class UserListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(
        default=None,
        max_length=USERNAME_MAX_LENGTH,
        description="帳號包含此文字；不分大小寫、重音與全形半形，% 與 _ 視為一般字元；空白視為未篩選",
    )
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    cursor: str | None = Field(
        default=None, max_length=CURSOR_MAX_LENGTH, description="上一次回應的 next_cursor；須搭配相同的 q"
    )


class RoleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role = Field(description="新角色：Admin、User 或 Viewer；於該使用者下一次請求生效，不需重新登入")


class ManagedUserOut(BaseModel):
    id: int
    username: str
    role: Role
    created_at: datetime = Field(description="註冊時間，一律為 UTC")
    last_login_at: datetime | None = Field(description="最後登入時間，一律為 UTC；null 表示尚無登入紀錄")
    is_self: bool = Field(description="是否為目前登入的 Admin 本人；本人不能變更自己的角色")

    @classmethod
    def from_user(cls, user: User, actor: User) -> "ManagedUserOut":
        return cls(
            id=user.id,
            username=user.username,
            role=user.role,
            created_at=user.created_at.replace(tzinfo=UTC),
            last_login_at=user.last_login_at.replace(tzinfo=UTC) if user.last_login_at else None,
            is_self=user.id == actor.id,
        )


class UserPage(BaseModel):
    items: list[ManagedUserOut] = Field(description="依註冊順序（舊到新）；可能為空，這時仍為 200")
    total: int = Field(description="符合搜尋條件的總人數，不受 cursor 影響")
    limit: int
    next_cursor: str | None = Field(description="下一頁的 cursor；null 表示沒有下一頁")
