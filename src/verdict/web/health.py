import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from verdict.core.db import ping_database
from verdict.web.deps import EngineDep, RedisDep

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health(engine: EngineDep, redis: RedisDep) -> JSONResponse:
    failing: list[str] = []
    try:
        ping_database(engine)
    except SQLAlchemyError:
        logger.warning("health check failed", extra={"component": "postgres"}, exc_info=True)
        failing.append("postgres")
    try:
        redis.ping()
    except RedisError:
        logger.warning("health check failed", extra={"component": "redis"}, exc_info=True)
        failing.append("redis")

    if failing:
        return JSONResponse({"status": "unavailable", "failing": failing}, status_code=503)
    return JSONResponse({"status": "ok"})
