"""User list and role changes (PRD-13). Only Admins reach these; the routes check that."""

import base64
import binascii
import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_capability
from app.auth.errors import FieldValidationError, NotAuthenticatedError, SessionLoggedOutError, SessionSupersededError
from app.auth.events import audit
from app.auth.permissions import Capability
from app.auth.validation import normalize_username
from app.models.user import Role, User
from app.users.errors import OwnRoleChangeForbiddenError, UserNotFoundError
from app.users.schemas import ManagedUserOut, UserListParams, UserPage


def _search_term(q: str | None) -> str | None:
    # The same folding as account names, so a full-width search finds a plain account.
    term = normalize_username(q).strip() if q else ""
    return term or None


def _invalid_cursor(reason: str) -> FieldValidationError:
    return FieldValidationError([{"field": "query.cursor", "message": reason, "type": "cursor_invalid"}])


def _encode_cursor(term: str | None, user_id: int) -> str:
    raw = json.dumps({"q": term, "i": user_id}, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, term: str | None) -> int:
    """Returns the id to continue after. Refuses a cursor from another search."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        cursor_term, user_id = payload["q"], payload["i"]
    except (ValueError, TypeError, KeyError, binascii.Error):
        raise _invalid_cursor("cursor 無效，請從第一頁重新查詢") from None
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise _invalid_cursor("cursor 無效，請從第一頁重新查詢")
    if cursor_term != term:
        raise _invalid_cursor("cursor 與目前的搜尋條件不一致，請從第一頁重新查詢")
    return user_id


async def list_users(session: AsyncSession, actor: User, params: UserListParams) -> UserPage:
    term = _search_term(params.q)
    # Compared under the column collation: case, accent and width insensitive.
    conditions = [User.username.contains(term, autoescape=True)] if term else []
    page_conditions = list(conditions)
    if params.cursor is not None:
        page_conditions.append(User.id > _decode_cursor(params.cursor, term))

    total = await session.scalar(select(func.count()).select_from(User).where(*conditions))
    # Ids grow with registration, so this is registration order, oldest first.
    rows = list(await session.scalars(select(User).where(*page_conditions).order_by(User.id).limit(params.limit + 1)))
    page = rows[: params.limit]
    has_next = len(rows) > params.limit
    return UserPage(
        items=[ManagedUserOut.from_user(user, actor) for user in page],
        total=total or 0,
        limit=params.limit,
        next_cursor=_encode_cursor(term, page[-1].id) if has_next else None,
    )


async def change_role(session: AsyncSession, actor: User, target_id: int, new_role: Role) -> ManagedUserOut:
    if target_id == actor.id:
        audit(
            "permission_denied",
            "permission denied",
            user=actor,
            level=logging.WARNING,
            role=actor.role.value,
            capability=Capability.SYSTEM_ADMIN.value,
            reason="own_role_change",
            target_user_id=target_id,
        )
        raise OwnRoleChangeForbiddenError()

    session_id = actor.current_session_id
    # Authentication left a read snapshot open. A locking read of a row changed since then
    # fails with error 1020 under innodb_snapshot_isolation, so start a fresh transaction.
    await session.commit()

    # Both rows locked in id order. Two Admins demoting each other at once are serialized, and
    # the second finds it is no longer Admin, so role changes can never remove the last Admin.
    locked = {
        user.id: user
        for user in await session.scalars(
            select(User)
            .where(User.id.in_([actor.id, target_id]))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    current_actor = locked.get(actor.id)
    if current_actor is None:
        raise NotAuthenticatedError()
    if current_actor.current_session_id is None:
        raise SessionLoggedOutError()
    if current_actor.current_session_id != session_id:
        raise SessionSupersededError()
    # Refuses, with the usual message and event, an Admin demoted while this request ran.
    await require_capability(Capability.SYSTEM_ADMIN)(current_actor)

    target = locked.get(target_id)
    if target is None:
        raise UserNotFoundError()
    old_role = target.role
    if old_role != new_role:
        target.role = new_role
    await session.commit()

    if old_role != new_role:
        audit(
            "user_role_changed",
            "user role changed",
            user=current_actor,
            target_user_id=target.id,
            target_username=target.username,
            old_role=old_role.value,
            new_role=new_role.value,
        )
    return ManagedUserOut.from_user(target, current_actor)
