from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from verdict.core.config import Settings, load_settings
from verdict.core.db import create_db_engine
from verdict.core.logs import configure_logging
from verdict.core.redis_client import create_redis
from verdict.web import health


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = create_db_engine(settings.database_url)
        app.state.redis = create_redis(settings.redis_url)
        try:
            yield
        finally:
            app.state.redis.close()
            app.state.engine.dispose()

    app = FastAPI(title="Verdict", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.include_router(health.router)
    return app
