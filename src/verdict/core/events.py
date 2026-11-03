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


def publish_submission_changed(redis: Redis, submission_id: int, contest_id: int | None) -> None:
    """Tell live pages a submission changed; they re-read it from Postgres, the source of truth."""
    message = json.dumps({"submission_id": submission_id})
    try:
        redis.publish(submission_channel(submission_id), message)
        if contest_id is not None:
            redis.publish(contest_channel(contest_id), message)
    except RedisError:
        # A lost event only delays a page until its next fetch, so judging carries on.
        logger.warning(
            "could not publish submission event",
            exc_info=True,
            extra={"submission_id": submission_id},
        )
