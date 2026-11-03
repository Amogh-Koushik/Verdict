import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from verdict.core.config import Settings, load_settings
from verdict.core.db import create_db_engine, create_session_factory
from verdict.core.logs import configure_logging
from verdict.core.redis_client import create_async_redis, create_redis
from verdict.web import (
    admin_contests,
    admin_problems,
    auth,
    contests,
    health,
    home,
    live,
    problems,
    submissions,
)
from verdict.web.errors import install_error_pages
from verdict.web.live import end_streams_on_shutdown

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = create_db_engine(settings.database_url)
        app.state.session_factory = create_session_factory(app.state.engine)
        app.state.redis = create_redis(settings.redis_url)
        app.state.async_redis = create_async_redis(settings.redis_url)
        app.state.stopping = threading.Event()
        end_streams_on_shutdown(app.state.stopping)
        try:
            yield
        finally:
            await app.state.async_redis.aclose()
            app.state.redis.close()
            app.state.engine.dispose()

    app = FastAPI(title="Verdict", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(home.router)
    app.include_router(auth.router)
    app.include_router(problems.router)
    app.include_router(submissions.router)
    app.include_router(contests.router)
    app.include_router(live.router)
    app.include_router(admin_problems.router)
    app.include_router(admin_contests.router)
    install_error_pages(app)
    return app
