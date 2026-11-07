from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.permissions import Capability
from app.core.errors import UNAVAILABLE_RESPONSE, ErrorResponse
from app.db.session import DbSession
from app.models.user import User
from app.records import service
from app.records.schemas import (
    AnomalyCheck,
    AnomalyCheckParams,
    CategoryList,
    RecordCreate,
    RecordListParams,
    RecordOut,
    RecordPage,
    RecordUpdate,
)

_NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "記錄已不存在，error.code 為 RECORD_NOT_FOUND"},
}
_CHANGE_FORBIDDEN_RESPONSE: dict[int | str, dict[str, Any]] = {
    403: {
        "model": ErrorResponse,
        "description": (
            "角色不可修改資料（FORBIDDEN）、不是此記錄的建立者（NOT_RECORD_OWNER），"
            "或記錄為異常、任何人都不能修改或刪除（ANOMALY_RECORD_READ_ONLY），error.message 說明原因"
        ),
    },
}
_CONFIRMATION_RESPONSE: dict[int | str, dict[str, Any]] = {
    409: {
        "model": ErrorResponse,
        "description": (
            "依目前範圍將成為異常但未帶 confirm_anomaly=true（ANOMALY_CONFIRMATION_REQUIRED），"
            "未寫入；error.details 含 category、value 與 range。"
            "新增時另可能回 CLIENT_REQUEST_REUSED：相同 client_request_id 但內容不同，未寫入"
        ),
    },
}

router = APIRouter(
    prefix="/records", tags=["records"], responses={**UNAVAILABLE_RESPONSE, **UNAUTHENTICATED_RESPONSE}
)

Reader = Annotated[User, Depends(require_capability(Capability.RECORDS_READ))]
Creator = Annotated[User, Depends(require_capability(Capability.RECORDS_CREATE))]
# Admin and User get past here. Ownership depends on the record, so the service checks it.
Modifier = Annotated[User, Depends(require_capability(Capability.RECORDS_MODIFY_OWN))]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="新增資料記錄",
    responses={**FORBIDDEN_RESPONSE, **_CONFIRMATION_RESPONSE},
)
async def create_record(payload: RecordCreate, user: Creator, session: DbSession) -> RecordOut:
    return await service.create_record(session, user, payload)


@router.get("", summary="分頁瀏覽、篩選與排序資料記錄")
async def list_records(params: Annotated[RecordListParams, Query()], _: Reader, session: DbSession) -> RecordPage:
    return await service.list_records(session, params)


@router.get("/categories", summary="資料中實際出現的分類")
async def list_categories(_: Reader, session: DbSession) -> CategoryList:
    return CategoryList(items=await service.list_categories(session))


@router.get(
    "/anomaly-check",
    summary="儲存前預判：此分類與數值依目前範圍是否會成為異常（修改時請傳修改後的分類與數值）",
    responses=FORBIDDEN_RESPONSE,
)
async def check_anomaly(params: Annotated[AnomalyCheckParams, Query()], _: Creator, session: DbSession) -> AnomalyCheck:
    return await service.check_anomaly(session, params)


@router.get("/{record_id}", summary="取得一筆資料記錄", responses=_NOT_FOUND_RESPONSE)
async def get_record(record_id: int, _: Reader, session: DbSession) -> RecordOut:
    return await service.get_record(session, record_id)


@router.patch(
    "/{record_id}",
    summary="修改資料記錄（僅建立者或 Admin）；未提供的欄位不變",
    responses={**_CHANGE_FORBIDDEN_RESPONSE, **_NOT_FOUND_RESPONSE, **_CONFIRMATION_RESPONSE},
)
async def update_record(record_id: int, payload: RecordUpdate, user: Modifier, session: DbSession) -> RecordOut:
    return await service.update_record(session, user, record_id, payload)


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="永久刪除資料記錄（僅建立者或 Admin）",
    responses={**_CHANGE_FORBIDDEN_RESPONSE, **_NOT_FOUND_RESPONSE},
)
async def delete_record(record_id: int, user: Modifier, session: DbSession) -> None:
    await service.delete_record(session, user, record_id)
