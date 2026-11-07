"""Registration, login, logout and token authentication."""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.errors import (
    FieldValidationError,
    InvalidCredentialsError,
    NotAuthenticatedError,
    SessionLoggedOutError,
    SessionSupersededError,
)
from app.auth.events import audit
from app.auth.passwords import hash_password, verify_against_dummy, verify_password
from app.auth.tokens import decode_token, issue_token
from app.auth.validation import normalize_username, password_problems, username_problems
from app.core.config import Settings
from app.core.errors import ConflictError
from app.models.user import Role, User


async def register_user(session: AsyncSession, username: str, password: str) -> User:
    username = normalize_username(username)
    problems = username_problems(username) + password_problems(password)
    if problems:
        raise FieldValidationError(problems)

    user = User(username=username, password_hash=await hash_password(password), role=Role.VIEWER)
    session.add(user)
    # No existence check first: the unique index also settles concurrent registrations.
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("此帳號已被使用", details={"field": "username", "reason": "already_exists"}) from None
    audit("user_registered", "account registered", user=user, role=user.role.value)
    return user


async def login(session: AsyncSession, username: str, password: str, settings: Settings) -> tuple[User, str, datetime]:
    user = await session.scalar(select(User).where(User.username == normalize_username(username)))
    # Ends the read transaction so that no connection is held while hashing.
    await session.commit()

    if user is None:
        await verify_against_dummy(password)
        # The submitted name is left out, since people sometimes type a password there.
        audit("login_failure", "login rejected", level=logging.WARNING, reason="unknown_account")
        raise InvalidCredentialsError()
    if not await verify_password(password, user.password_hash):
        audit("login_failure", "login rejected", user=user, level=logging.WARNING, reason="wrong_password")
        raise InvalidCredentialsError()

    displaced = user.current_session_id is not None
    session_id = uuid.uuid4().hex
    # One atomic write: when logins race, the last one to commit is the valid one.
    await session.execute(update(User).where(User.id == user.id).values(current_session_id=session_id))
    await session.commit()

    token, expires_at = issue_token(user.id, session_id, settings)
    if displaced:
        audit("login_superseded", "earlier login invalidated by a new login", user=user)
    audit("login_success", "login succeeded", user=user, role=user.role.value)
    return user, token, expires_at


async def logout(session: AsyncSession, user: User) -> None:
    session_id = user.current_session_id
    # The condition keeps a login that raced ahead of this logout valid.
    await session.execute(
        update(User).where(User.id == user.id, User.current_session_id == session_id).values(current_session_id=None)
    )
    await session.commit()
    audit("logout", "logged out", user=user)


async def authenticate_token(session: AsyncSession, token: str, settings: Settings) -> User:
    """Reads the role and login validity from the database. Also usable from WebSocket routes."""
    claims = decode_token(token, settings)
    user = await session.get(User, claims.user_id)
    if user is None:
        raise NotAuthenticatedError()
    if user.current_session_id is None:
        raise SessionLoggedOutError()
    if user.current_session_id != claims.session_id:
        raise SessionSupersededError()
    return user
