from fastapi import APIRouter

from app.api.v1 import auth, categories, health, records
from app.core.errors import ERROR_RESPONSES

api_router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(records.router)
api_router.include_router(categories.router)
