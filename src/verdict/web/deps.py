from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from verdict.core.config import Settings
from verdict.core.models import User
from verdict.web.sessions import SESSION_COOKIE, BrowserSession, create_session, load_session


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def get_db(request: Request) -> Iterator[Session]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as db:
        yield db


SettingsDep = Annotated[Settings, Depends(get_settings)]
EngineDep = Annotated[Engine, Depends(get_engine)]
RedisDep = Annotated[Redis, Depends(get_redis)]
DbDep = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class Viewer:
    """Who is making the request: their browser session, if any, and the logged-in user."""

    session: BrowserSession | None
    user: User | None
    session_is_new: bool = False


def get_viewer(request: Request, redis: RedisDep, db: DbDep) -> Viewer:
    token = request.cookies.get(SESSION_COOKIE)
    session = load_session(redis, token) if token else None
    user = None
    if session is not None and session.user_id is not None:
        user = db.get(User, session.user_id)
    return Viewer(session=session, user=user)


ViewerDep = Annotated[Viewer, Depends(get_viewer)]


def get_form_viewer(viewer: ViewerDep, redis: RedisDep, settings: SettingsDep) -> Viewer:
    """Like get_viewer, but starts an anonymous session so the page's forms get a CSRF token."""
    if viewer.session is not None:
        return viewer
    session = create_session(redis, user_id=None, ttl_seconds=settings.session_ttl_seconds)
    return Viewer(session=session, user=None, session_is_new=True)


FormViewerDep = Annotated[Viewer, Depends(get_form_viewer)]
