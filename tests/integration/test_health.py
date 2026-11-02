from dataclasses import replace

from fastapi.testclient import TestClient

from verdict.core.config import load_settings
from verdict.web.main import create_app

UNREACHABLE_POSTGRES = "postgresql+psycopg://verdict:verdict@127.0.0.1:1/verdict"
UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"


def get_health(**overrides: str) -> tuple[int, dict[str, object]]:
    settings = replace(load_settings(), **overrides)
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    return response.status_code, response.json()


def test_health_is_ok_when_postgres_and_redis_respond() -> None:
    assert get_health() == (200, {"status": "ok"})


def test_health_names_postgres_when_it_is_unreachable() -> None:
    status, body = get_health(database_url=UNREACHABLE_POSTGRES)
    assert status == 503
    assert body == {"status": "unavailable", "failing": ["postgres"]}


def test_health_names_redis_when_it_is_unreachable() -> None:
    status, body = get_health(redis_url=UNREACHABLE_REDIS)
    assert status == 503
    assert body == {"status": "unavailable", "failing": ["redis"]}


def test_health_names_both_when_neither_is_reachable() -> None:
    status, body = get_health(database_url=UNREACHABLE_POSTGRES, redis_url=UNREACHABLE_REDIS)
    assert status == 503
    assert body == {"status": "unavailable", "failing": ["postgres", "redis"]}
