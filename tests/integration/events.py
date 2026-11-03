import time
from typing import Any

from redis.client import PubSub


def next_event(events: PubSub, timeout_seconds: float = 2) -> dict[str, Any] | None:
    # get_message returns None for the subscribe confirmation, so keep reading until the deadline.
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        message = events.get_message(timeout=deadline - time.monotonic())
        if message is not None:
            return message
    return None
