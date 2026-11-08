import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, Query, Request, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.auth.dependencies import FORBIDDEN_RESPONSE, UNAUTHENTICATED_RESPONSE, require_capability
from app.auth.errors import NotAuthenticatedError
from app.auth.permissions import Capability, user_can
from app.auth.service import authenticate_token
from app.core.config import get_settings
from app.core.errors import UNAVAILABLE_RESPONSE, AppError, ErrorResponse, ForbiddenError
from app.db.session import DbSession
from app.models.user import User
from app.realtime import history
from app.realtime.hub import Close, RealtimeHub, Subscriber
from app.realtime.schemas import LiveSnapshot, RangeParams, RangeView

AUTH_TIMEOUT_SECONDS = 5.0
HEARTBEAT_SECONDS = 10.0

WS_CONTRACT = """
即時窗口快照（PRD-08）。所有已登入角色可用。畫面可每數秒輪詢此端點，或改用 WebSocket；兩者內容相同。

- `summary.stalled` 為 true（server_time 與 last_reading_at 相差超過 stale_after_seconds = 2 × interval + 10）：
  連線正常但資料停止到達，顯示「已暫停更新」。請求失敗（連線錯誤、逾時、5xx）：顯示「重新連線中」並重試。
  401（SESSION_SUPERSEDED 等）：導向登入。
- 即時窗口只含系統產生的模擬資料；讀數先推送、約 5 秒內批次寫入資料庫。

**WebSocket `/api/v1/realtime/ws`**

- 認證：`Authorization: Bearer <token>` 標頭，或連線後 5 秒內送出 `{"type":"auth","token":"<token>"}`。不接受網址中的 token。
- 伺服器訊息（JSON，`type` 區分）：
  - `snapshot`：認證後一次，內容同本端點回應。每次（重新）連線都會收到新的快照。
  - `readings`：每次產生資料一則（每個設備一筆），含 `readings[]`、`summary`、`category_anomaly_counts_5m`、`simulated: true`。
    客戶端自行移除超過 300 秒的點。
  - `heartbeat`：每 10 秒，含 `server_time`、`last_reading_at`、`generator`。超過 25 秒沒有任何訊息即視為斷線。
  - `error`：關閉前送出，`error` 同統一錯誤格式（code、message）。
- 客戶端只能送 `auth`；其他訊息以 1008 關閉。
- 關閉代碼：4401 登入無效／過期／在別處登入／已登出（reason 為 error.code，不要重連，導向登入）；
  4403 角色不可檢視；4408 未在 5 秒內認證；1008 協定錯誤；
  1001／1012 伺服器關閉或重啟、1011 伺服器錯誤、1013 客戶端讀取太慢：以 1、2、4、8 秒再每 15 秒退避重連。
"""

router = APIRouter(prefix="/realtime", tags=["realtime"], responses={**UNAVAILABLE_RESPONSE, **UNAUTHENTICATED_RESPONSE})

Viewer = Annotated[User, Depends(require_capability(Capability.MONITORING_VIEW))]


class RealtimeUnavailableError(AppError):
    status_code = 503
    code = "REALTIME_UNAVAILABLE"
    message = "即時服務尚未啟動"


def _hub(app: Any) -> RealtimeHub:
    hub = getattr(app.state, "realtime_hub", None)
    if hub is None:
        raise RealtimeUnavailableError()
    return hub


@router.get("/window", summary="即時窗口：最近 5 分鐘的讀數、摘要與最近異常（含 WebSocket 契約說明）", description=WS_CONTRACT, responses=FORBIDDEN_RESPONSE)
async def live_window(request: Request, _: Viewer) -> LiveSnapshot:
    return _hub(request.app).snapshot()


@router.get(
    "/range",
    summary="自訂時間範圍：已保存資料的彙總圖表資料與摘要（最長 3 個月，不自動更新）",
    description=(
        "start、end 為含時區位移的 ISO-8601。end 不可晚於 start 的 3 個日曆月後（以 start 的時區計算，"
        "日期超過月底時取月底；剛好 3 個月可以查詢）。超過時回 422，error.code 為 RANGE_TOO_LONG，"
        "details 含 max_months 與 latest_allowed_end。每個分類不超過 400 點時逐筆回傳，否則依序選用 "
        "1、5、15、30 分鐘、1、3、6、12 小時、1 天的區間彙總（aggregation 說明所用區間）；"
        "含異常的區間 anomaly_count 大於 0。"
    ),
    responses={**FORBIDDEN_RESPONSE, 422: {"model": ErrorResponse, "description": "VALIDATION_ERROR 或 RANGE_TOO_LONG"}},
)
async def custom_range(params: Annotated[RangeParams, Query()], _: Viewer, session: DbSession) -> RangeView:
    return await history.range_view(session, params)


# --- WebSocket ---


def _bearer(websocket: WebSocket) -> str | None:
    scheme, _, token = websocket.headers.get("authorization", "").partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


async def _first_message_token(websocket: WebSocket) -> str | None:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    try:
        data = json.loads(message.get("text") or "")
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("type") == "auth" and isinstance(data.get("token"), str):
        return data["token"]
    return None


async def _close(websocket: WebSocket, code: int, error: AppError | None = None) -> None:
    if websocket.client_state != WebSocketState.CONNECTED:
        return
    with contextlib.suppress(Exception):
        if error is not None:
            await websocket.send_json(RealtimeHub.error_message(error))
        await websocket.close(code=code, reason=error.code if error else "")


async def _authenticate(websocket: WebSocket, hub: RealtimeHub) -> tuple[User, str, datetime] | None:
    token = _bearer(websocket)
    if token is None:
        try:
            token = await asyncio.wait_for(_first_message_token(websocket), AUTH_TIMEOUT_SECONDS)
        except TimeoutError:
            await _close(websocket, 4408, AppError("未在時限內完成認證", code="AUTH_TIMEOUT", status_code=401))
            return None
    if not token:
        await _close(websocket, 4401, NotAuthenticatedError())
        return None
    try:
        async with hub.session_factory() as session:
            user = await authenticate_token(session, token, get_settings())
            session_id = user.current_session_id
            await session.commit()
    except AppError as error:
        await _close(websocket, 4401, error)
        return None
    if not user_can(user, Capability.MONITORING_VIEW):
        await _close(websocket, 4403, ForbiddenError())
        return None
    # The signature was verified above; this only reads the expiry for the session sweep.
    expires = jwt.decode(token, options={"verify_signature": False})["exp"]
    return user, session_id, datetime.fromtimestamp(expires, UTC)


async def _pump(websocket: WebSocket, hub: RealtimeHub, subscriber: Subscriber) -> None:
    while True:
        try:
            message = await asyncio.wait_for(subscriber.queue.get(), HEARTBEAT_SECONDS)
        except TimeoutError:
            message = hub.heartbeat_message()
        if isinstance(message, Close):
            await _close(websocket, message.code, message.error)
            return
        await websocket.send_json(message)


async def _listen(websocket: WebSocket) -> bool:
    """Returns True when the client sent something it must not send."""
    message = await websocket.receive()
    return message["type"] != "websocket.disconnect"


@router.websocket("/ws")
async def live_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    hub = getattr(websocket.app.state, "realtime_hub", None)
    if hub is None:
        await _close(websocket, 1011, RealtimeUnavailableError())
        return
    try:
        authenticated = await _authenticate(websocket, hub)
    except WebSocketDisconnect:
        return
    if authenticated is None:
        return
    user, session_id, expires_at = authenticated
    subscriber = hub.subscribe(user.id, session_id, expires_at)
    try:
        await websocket.send_json(hub.snapshot_message())
        pump = asyncio.create_task(_pump(websocket, hub, subscriber))
        listen = asyncio.create_task(_listen(websocket))
        done, pending = await asyncio.wait({pump, listen}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if pump in done and not pump.cancelled():
            pump.exception()  # A failed send means the client is gone; nothing more to do.
        if listen in done and not listen.cancelled() and listen.exception() is None and listen.result():
            await _close(websocket, 1008, AppError("只接受 auth 訊息", code="PROTOCOL_ERROR", status_code=400))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(subscriber)
