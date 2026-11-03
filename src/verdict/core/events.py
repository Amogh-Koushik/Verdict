import json
import logging

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Postgres NOTIFY channel: the web app signals it after queueing a submission, workers LISTEN.
QUEUE_CHANNEL = "submission_queued"


def submission_channel(submission_id: int) -> str:
    return f"submission:{submission_id}"


def contest_channel(contest_id: int) -> str:
    return f"contest:{contest_id}"


def scoreboard_key(contest_id: int) -> str:
    return f"scoreboard:{contest_id}"


def scoreboard_generation_key(contest_id: int) -> str:
    """Counts the contest's changes, so a scoreboard computed before one is never served after."""
    return f"scoreboard:{contest_id}:generation"


def publish_submission_changed(redis: Redis, submission_id: int, contest_id: int | None) -> None:
    """Tell live pages a submission changed; they re-read it from Postgres, the source of truth."""
    try:
        redis.publish(
            submission_channel(submission_id), json.dumps({"submission_id": submission_id})
        )
        if contest_id is not None:
            _announce_contest_change(redis, contest_id)
    except RedisError:
        # A lost event only delays a page until its next fetch, so judging carries on.
        logger.warning(
            "could not publish submission event",
            exc_info=True,
            extra={"submission_id": submission_id},
        )


def publish_contest_changed(redis: Redis, contest_id: int) -> None:
    """Tell live scoreboards the contest's times or problems changed."""
    try:
        _announce_contest_change(redis, contest_id)
    except RedisError:
        logger.warning(
            "could not publish contest event", exc_info=True, extra={"contest_id": contest_id}
        )


def _announce_contest_change(redis: Redis, contest_id: int) -> None:
    # One transaction: the cached scoreboard is gone before any page hears about the change.
    with redis.pipeline() as pipeline:
        pipeline.delete(scoreboard_key(contest_id))
        pipeline.incr(scoreboard_generation_key(contest_id))
        pipeline.publish(contest_channel(contest_id), json.dumps({"contest_id": contest_id}))
        pipeline.execute()
