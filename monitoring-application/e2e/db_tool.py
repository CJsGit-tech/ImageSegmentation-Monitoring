"""Test-only helper, run inside the backend container by conftest.py. Never part of the app.

    promote <username> <Admin|User|Viewer>   there is no role API until the user-management screen
    cleanup <prefix> <category-prefix>       removes what one test run created, including its imports

cleanup refuses any prefix that does not start with "e2e-", so it cannot touch other data.
"""

import asyncio
import sys

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_engine
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.record_import import RecordImport
from app.models.user import Role, User

RUN_PREFIX = "e2e-"


async def promote(username: str, role: str) -> None:
    async with AsyncSession(get_engine()) as session:
        user = await session.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit(f"no such user: {username}")
        user.role = Role(role)
        await session.commit()


async def cleanup(prefix: str, category_prefix: str) -> None:
    if not prefix.startswith(RUN_PREFIX) or RUN_PREFIX not in category_prefix:
        raise SystemExit(f"refusing to clean up a prefix that is not a test run: {prefix!r} {category_prefix!r}")
    async with AsyncSession(get_engine()) as session:
        users = select(User.id).where(User.username.startswith(prefix, autoescape=True))
        records = await session.execute(
            delete(Record).where(
                or_(Record.category.startswith(category_prefix, autoescape=True), Record.created_by_user_id.in_(users))
            )
        )
        settings = await session.execute(
            delete(CategorySetting).where(CategorySetting.category.startswith(category_prefix, autoescape=True))
        )
        imports = await session.execute(delete(RecordImport).where(RecordImport.imported_by_user_id.in_(users)))
        accounts = await session.execute(delete(User).where(User.username.startswith(prefix, autoescape=True)))
        await session.commit()
        print(f"removed {records.rowcount} records, {settings.rowcount} settings, {imports.rowcount} imports, {accounts.rowcount} accounts")


if __name__ == "__main__":
    command, *args = sys.argv[1:]
    asyncio.run({"promote": promote, "cleanup": cleanup}[command](*args))
