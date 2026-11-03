import json

from redis import Redis
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from tests.integration.events import next_event
from tests.integration.submissions import add_submissions
from verdict.core.config import Settings
from verdict.core.events import submission_channel
from verdict.core.models import Submission
from verdict.core.verdicts import SubmissionStatus, Verdict
from verdict.judge.queue import claim_submission
from verdict.judge.worker import claim_next_submission


def stall_on_every_attempt(engine: Engine, submission_id: int, attempts: int) -> None:
    """Claim the submission `attempts` times, each time letting the lease run out."""
    for attempt in range(1, attempts + 1):
        claimed = claim_submission(engine, f"crashing-worker-{attempt}", lease_seconds=60)
        assert claimed is not None
        assert claimed.lease.attempt == attempt
        with engine.begin() as connection:
            connection.execute(
                update(Submission)
                .where(Submission.id == submission_id)
                .values(lease_expires_at=Submission.created_at)
            )


def test_a_submission_over_max_attempts_becomes_failed_with_a_system_error(
    engine: Engine, db: Session, redis: Redis, settings: Settings
) -> None:
    poison, next_in_line = add_submissions(db, 2)
    stall_on_every_attempt(engine, poison, attempts=settings.max_attempts)
    events = redis.pubsub(ignore_subscribe_messages=True)
    events.subscribe(submission_channel(poison))

    claimed = claim_next_submission(engine, redis, "healthy-worker", settings)

    assert claimed is not None
    assert claimed.lease.submission_id == next_in_line
    row = db.get_one(Submission, poison)
    db.refresh(row)
    assert row.status is SubmissionStatus.FAILED
    assert row.verdict is Verdict.SYSTEM_ERROR
    assert row.attempt == settings.max_attempts + 1
    message = next_event(events)
    assert message is not None
    assert json.loads(message["data"]) == {"submission_id": poison}
    events.close()


def test_a_submission_within_max_attempts_is_judged_again(
    engine: Engine, db: Session, redis: Redis, settings: Settings
) -> None:
    [submission_id] = add_submissions(db, 1)
    stall_on_every_attempt(engine, submission_id, attempts=settings.max_attempts - 1)

    claimed = claim_next_submission(engine, redis, "healthy-worker", settings)

    assert claimed is not None
    assert claimed.lease.submission_id == submission_id
    assert claimed.lease.attempt == settings.max_attempts
