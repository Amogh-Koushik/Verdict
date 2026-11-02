from typing import Annotated

from fastapi import Depends, Request
from redis import Redis
from sqlalchemy import Engine


def get_engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


EngineDep = Annotated[Engine, Depends(get_engine)]
RedisDep = Annotated[Redis, Depends(get_redis)]
