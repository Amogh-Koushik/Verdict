import time

import pytest
from redis import Redis

from verdict.web.rate_limit import RateLimitExceededError, hit_fixed_window


def wait_for_next_window(window_seconds: int) -> None:
    time.sleep(window_seconds - time.time() % window_seconds)


def test_hits_up_to_the_limit_are_allowed_and_the_next_is_refused(redis: Redis) -> None:
    wait_for_next_window(2)
    for _ in range(3):
        hit_fixed_window(redis, "test", limit=3, window_seconds=2)
    with pytest.raises(RateLimitExceededError) as refused:
        hit_fixed_window(redis, "test", limit=3, window_seconds=2)
    assert 1 <= refused.value.retry_after_seconds <= 2


def test_counter_starts_again_in_the_next_window(redis: Redis) -> None:
    wait_for_next_window(1)
    hit_fixed_window(redis, "test", limit=1, window_seconds=1)
    wait_for_next_window(1)
    hit_fixed_window(redis, "test", limit=1, window_seconds=1)


def test_each_key_has_its_own_counter(redis: Redis) -> None:
    wait_for_next_window(2)
    hit_fixed_window(redis, "first", limit=1, window_seconds=2)
    hit_fixed_window(redis, "second", limit=1, window_seconds=2)


def test_counters_expire_with_their_window(redis: Redis) -> None:
    hit_fixed_window(redis, "test", limit=5, window_seconds=60)
    [key] = redis.keys("ratelimit:test:*")
    assert 0 < int(redis.ttl(key)) <= 60
