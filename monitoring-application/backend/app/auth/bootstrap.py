"""Initial Admin (PRD-01-R8): created when absent, otherwise left exactly as it is."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.events import audit
from app.auth.passwords import hash_password
from app.auth.validation import normalize_username, password_problems, username_problems
from app.core.config import ConfigurationError, Settings
from app.models.user import USERNAME_MAX_LENGTH, Role, User


async def ensure_initial_admin(engine: AsyncEngine, settings: Settings) -> None:
    username = normalize_username(settings.initial_admin_username)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        existing = await session.scalar(select(User).where(User.username == username))
        if existing is not None:
            if existing.role is Role.ADMIN:
                audit("initial_admin_exists", "initial admin already exists", user=existing)
            else:
                audit(
                    "initial_admin_not_admin",
                    "INITIAL_ADMIN_USERNAME belongs to an existing account that is not Admin; it was not changed",
                    user=existing,
                    level=logging.WARNING,
                    role=existing.role.value,
                )
            return

        if len(username) > USERNAME_MAX_LENGTH:
            raise ConfigurationError(f"Invalid environment variables: INITIAL_ADMIN_USERNAME (longer than {USERNAME_MAX_LENGTH} characters)")
        _warn_about_format(settings, username)

        password = settings.initial_admin_password.get_secret_value()
        user = User(username=username, password_hash=await hash_password(password), role=Role.ADMIN)
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            # Another backend instance created it first.
            await session.rollback()
            return
        audit("initial_admin_created", "initial admin created", user=user)


def _warn_about_format(settings: Settings, username: str) -> None:
    """The format rules bind registration only (PRD-01-R11), so these do not stop startup."""
    password_conditions = [p["type"] for p in password_problems(settings.initial_admin_password.get_secret_value())]
    if password_conditions:
        audit(
            "initial_admin_weak_password",
            "INITIAL_ADMIN_PASSWORD does not meet the registration password rules",
            level=logging.WARNING,
            variable="INITIAL_ADMIN_PASSWORD",
            missing_conditions=password_conditions,
        )
    username_conditions = [p["type"] for p in username_problems(username)]
    if username_conditions:
        audit(
            "initial_admin_unusual_username",
            "INITIAL_ADMIN_USERNAME does not meet the registration account rules",
            level=logging.WARNING,
            variable="INITIAL_ADMIN_USERNAME",
            missing_conditions=username_conditions,
        )
