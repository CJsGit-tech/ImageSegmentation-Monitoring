from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from app.auth.permissions import Capability, capabilities_for
from app.models.user import Role, User

# Bounds only. They reveal nothing about accounts, and format rules apply at registration.
_LOGIN_USERNAME_MAX_LENGTH = 256
_LOGIN_PASSWORD_MAX_LENGTH = 1024


def _require_encodable(value: str) -> str:
    # JSON can carry unpaired surrogates, which can be neither hashed nor sent to the database.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("contains invalid characters") from None
    return value


TextField = Annotated[str, AfterValidator(_require_encodable)]


class RegisterRequest(BaseModel):
    username: TextField = Field(description="2 到 64 個字元；可用任何語言的文字、數字與 _ . - @，大小寫視為相同")
    password: TextField = Field(description="8 到 128 個字元，需同時包含英文大寫、英文小寫、數字與符號")


class LoginRequest(BaseModel):
    username: TextField = Field(min_length=1, max_length=_LOGIN_USERNAME_MAX_LENGTH)
    password: TextField = Field(min_length=1, max_length=_LOGIN_PASSWORD_MAX_LENGTH)


class UserOut(BaseModel):
    id: int
    username: str
    role: Role
    capabilities: list[Capability]
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            username=user.username,
            role=user.role,
            capabilities=capabilities_for(user.role),
            created_at=user.created_at.replace(tzinfo=UTC),
        )


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut
