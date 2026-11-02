import math
import time

from redis import Redis


class RateLimitExceededError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"rate limit exceeded; retry after {retry_after_seconds} s")
        self.retry_after_seconds = retry_after_seconds


def hit_fixed_window(redis: Redis, key: str, limit: int, window_seconds: int) -> None:
    """Count one hit for `key` in the current window; raise once the window holds over `limit`."""
    now = time.time()
    window = int(now // window_seconds)
    window_key = f"ratelimit:{key}:{window}"
    with redis.pipeline() as pipe:
        pipe.incr(window_key)
        # NX sets the expiry on the first hit only, so later hits don't extend the window.
        pipe.expire(window_key, window_seconds, nx=True)
        count, _ = pipe.execute()
    if count > limit:
        window_ends_at = (window + 1) * window_seconds
        raise RateLimitExceededError(max(1, math.ceil(window_ends_at - now)))
