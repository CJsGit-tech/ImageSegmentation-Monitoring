from fastapi import APIRouter

from app.api.v1 import analytics, auth, categories, db_status, exports, health, history, imports, logs, realtime, records, users
from app.core.errors import ERROR_RESPONSES

api_router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(imports.router)
api_router.include_router(records.router)
api_router.include_router(categories.router)
api_router.include_router(realtime.router)
api_router.include_router(analytics.router)
api_router.include_router(exports.router)
api_router.include_router(users.router)
api_router.include_router(logs.router)
api_router.include_router(db_status.router)
api_router.include_router(history.router)
