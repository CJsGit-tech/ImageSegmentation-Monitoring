from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.db.session import get_engine, get_session_factory
from app.realtime.runtime import start_realtime
from app.syslog.runtime import start_syslog


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # First in, last out, so that simulator start, stop and failures reach the system log.
    syslog = await start_syslog(get_session_factory(), get_settings().log_retention_days)
    realtime = await start_realtime(app)
    yield
    await realtime.stop()
    await syslog.stop()
    await get_engine().dispose()


def create_app() -> FastAPI:
    configure_logging(get_settings().log_level)
    app = FastAPI(
        title="MonitoringDashboard API",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
