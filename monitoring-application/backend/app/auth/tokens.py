"""JWT creation and verification. Login validity itself is checked against the database."""

from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from app.auth.errors import NotAuthenticatedError, TokenExpiredError
from app.core.config import Settings

ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ["sub", "sid", "iat", "exp"]


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    session_id: str


def issue_token(user_id: int, session_id: str, settings: Settings) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    # JWT times are whole seconds; expires_at reports the same value the token holds.
    expires_at = datetime.fromtimestamp(int((now + settings.access_token_expire_delta).timestamp()), UTC)
    claims = {"sub": str(user_id), "sid": session_id, "iat": now, "exp": expires_at}
    token = jwt.encode(claims, settings.jwt_secret_key.get_secret_value(), algorithm=ALGORITHM)
    return token, expires_at


def decode_token(token: str, settings: Settings) -> TokenClaims:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[ALGORITHM],
            options={"require": _REQUIRED_CLAIMS},
        )
        user_id = int(claims["sub"])
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError() from None
    except (jwt.InvalidTokenError, ValueError):
        raise NotAuthenticatedError() from None
    session_id = claims["sid"]
    if not isinstance(session_id, str):
        raise NotAuthenticatedError()
    return TokenClaims(user_id=user_id, session_id=session_id)
