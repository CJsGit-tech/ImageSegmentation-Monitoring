"""Initial accounts (PRD-01-R8): each is created when absent, otherwise left exactly as it is.

The Admin, User and Viewer test accounts all come from the environment (.env). They are created
at startup, before the port opens, so that nobody can register one of these names first.
"""

import logging
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.events import audit
from app.auth.passwords import hash_password
from app.auth.validation import normalize_username, password_problems, username_problems
from app.core.config import ConfigurationError, Settings
from app.models.user import USERNAME_MAX_LENGTH, Role, User


@dataclass(frozen=True)
class InitialAccount:
    role: Role
    # Lower-case name used in event types ("initial_admin_created") and, upper-cased, in variables.
    key: str
    username: str
    password: SecretStr

    @property
    def username_variable(self) -> str:
        return f"INITIAL_{self.key.upper()}_USERNAME"

    @property
    def password_variable(self) -> str:
        return f"INITIAL_{self.key.upper()}_PASSWORD"


def initial_accounts(settings: Settings) -> tuple[InitialAccount, ...]:
    return (
        InitialAccount(Role.ADMIN, "admin", settings.initial_admin_username, settings.initial_admin_password),
        InitialAccount(Role.USER, "user", settings.initial_user_username, settings.initial_user_password),
        InitialAccount(Role.VIEWER, "viewer", settings.initial_viewer_username, settings.initial_viewer_password),
    )


async def ensure_initial_accounts(engine: AsyncEngine, settings: Settings) -> None:
    accounts = initial_accounts(settings)
    _check_distinct(accounts)
    for account in accounts:
        await _ensure_account(engine, account)


async def ensure_initial_admin(engine: AsyncEngine, settings: Settings) -> None:
    await _ensure_account(engine, initial_accounts(settings)[0])


def _check_distinct(accounts: tuple[InitialAccount, ...]) -> None:
    # The username column compares case-insensitively, so two names differing only in case clash.
    seen: dict[str, InitialAccount] = {}
    for account in accounts:
        folded = normalize_username(account.username).casefold()
        if folded in seen:
            raise ConfigurationError(
                f"Invalid environment variables: {account.username_variable} "
                f"(same account as {seen[folded].username_variable})"
            )
        seen[folded] = account


async def _ensure_account(engine: AsyncEngine, account: InitialAccount) -> None:
    username = normalize_username(account.username)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        existing = await session.scalar(select(User).where(User.username == username))
        if existing is not None:
            if existing.role is account.role:
                audit(f"initial_{account.key}_exists", f"initial {account.key} already exists", user=existing)
            else:
                audit(
                    f"initial_{account.key}_wrong_role",
                    f"{account.username_variable} belongs to an existing account whose role is not "
                    f"{account.role.value}; it was not changed",
                    user=existing,
                    level=logging.WARNING,
                    role=existing.role.value,
                )
            return

        if len(username) > USERNAME_MAX_LENGTH:
            raise ConfigurationError(
                f"Invalid environment variables: {account.username_variable} (longer than {USERNAME_MAX_LENGTH} characters)"
            )
        _warn_about_format(account, username)

        password = account.password.get_secret_value()
        user = User(username=username, password_hash=await hash_password(password), role=account.role)
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            # Another backend instance created it first.
            await session.rollback()
            return
        audit(f"initial_{account.key}_created", f"initial {account.key} created", user=user)


def _warn_about_format(account: InitialAccount, username: str) -> None:
    """The format rules bind registration only (PRD-01-R11), so these do not stop startup."""
    password_conditions = [p["type"] for p in password_problems(account.password.get_secret_value())]
    if password_conditions:
        audit(
            f"initial_{account.key}_weak_password",
            f"{account.password_variable} does not meet the registration password rules",
            level=logging.WARNING,
            variable=account.password_variable,
            missing_conditions=password_conditions,
        )
    username_conditions = [p["type"] for p in username_problems(username)]
    if username_conditions:
        audit(
            f"initial_{account.key}_unusual_username",
            f"{account.username_variable} does not meet the registration account rules",
            level=logging.WARNING,
            variable=account.username_variable,
            missing_conditions=username_conditions,
        )
