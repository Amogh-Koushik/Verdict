from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import verdict.core.models  # noqa: F401  (registers the tables on Base.metadata)
from verdict.core.config import Settings, load_settings
from verdict.core.db import Base, create_db_engine, create_session_factory
from verdict.core.redis_client import create_redis
from verdict.web.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE = "verdict_test"
# Database 15 keeps test keys apart from a development stack, which uses database 0.
TEST_REDIS_DB = 15


def recreate_database(server_url: str, name: str) -> str:
    """Drop and recreate database `name` on the server `server_url` points at; return its URL."""
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return make_url(server_url).set(database=name).render_as_string(hide_password=False)


def run_alembic(engine: Engine, action: Callable[[Config, str], None], revision: str) -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        action(config, revision)


@pytest.fixture(scope="session")
def settings() -> Settings:
    base = load_settings()
    redis_url = urlsplit(base.redis_url)._replace(path=f"/{TEST_REDIS_DB}").geturl()
    database_url = make_url(base.database_url).set(database=TEST_DATABASE)
    return replace(
        base,
        database_url=database_url.render_as_string(hide_password=False),
        redis_url=redis_url,
    )


@pytest.fixture(scope="session")
def migrated_engine(settings: Settings) -> Iterator[Engine]:
    recreate_database(load_settings().database_url, TEST_DATABASE)
    engine = create_db_engine(settings.database_url)
    run_alembic(engine, command.upgrade, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def engine(migrated_engine: Engine) -> Engine:
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with migrated_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return migrated_engine


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with create_session_factory(engine)() as session:
        yield session


@pytest.fixture
def redis(settings: Settings) -> Iterator[Redis]:
    client = create_redis(settings.redis_url)
    client.flushdb()
    yield client
    client.close()


@pytest.fixture
def app_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Settings for one test, with its own empty folder for uploaded tests."""
    return replace(settings, problems_dir=tmp_path / "problems")


@pytest.fixture
def client(app_settings: Settings, engine: Engine, redis: Redis) -> Iterator[TestClient]:
    with TestClient(create_app(app_settings)) as test_client:
        yield test_client
