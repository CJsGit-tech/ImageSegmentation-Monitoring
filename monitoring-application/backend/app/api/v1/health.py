from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import literal, select

from app import __version__
from app.core.errors import UNAVAILABLE_RESPONSE, AppError
from app.db.migrate import current_revision, expected_revision
from app.db.session import DbSession

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    database: Literal["ok"] = "ok"
    schema_revision: str | None


@router.get("", summary="後端存活檢查（不連資料庫）")
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@router.get("/ready", summary="就緒檢查（資料庫可連線且結構為最新）", responses=UNAVAILABLE_RESPONSE)
async def ready(session: DbSession) -> ReadyResponse:
    await session.execute(select(literal(1)))
    revision = await current_revision(await session.connection())
    if revision != expected_revision():
        raise AppError("資料庫結構不是最新版本", code="SCHEMA_OUT_OF_DATE", status_code=503)
    return ReadyResponse(schema_revision=revision)
