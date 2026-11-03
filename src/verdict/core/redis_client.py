from redis import Redis
from redis.asyncio import Redis as AsyncRedis


def create_redis(redis_url: str) -> Redis:
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


def create_async_redis(redis_url: str) -> AsyncRedis:
    # No read timeout: a pub/sub connection is idle by design between keep-alives.
    return AsyncRedis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3)
