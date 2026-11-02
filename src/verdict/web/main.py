from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from verdict.core.config import Settings, load_settings
from verdict.core.db import create_db_engine, create_session_factory
from verdict.core.logs import configure_logging
from verdict.core.redis_client import create_redis
from verdict.web import admin_problems, auth, health, home, problems
from verdict.web.errors import install_error_pages

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
        try:
            yield
        finally:
            app.state.redis.close()
            app.state.engine.dispose()

    app = FastAPI(title="Verdict", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(home.router)
    app.include_router(auth.router)
    app.include_router(problems.router)
    app.include_router(admin_problems.router)
    install_error_pages(app)
    return app
