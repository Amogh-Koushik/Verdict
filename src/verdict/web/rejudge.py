from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from verdict.core.events import QUEUE_CHANNEL

# attempt goes back to 0 so the run gets its full MAX_ATTEMPTS. A worker still judging the old
# run is fenced out all the same: its writes also require status 'judging' and its lease_owner.
REQUEUE_PROBLEM = text(
    """
    UPDATE submissions
    SET status = 'queued', attempt = 0, verdict = NULL, failed_test = NULL, time_ms = NULL,
        memory_kb = NULL, compile_output = NULL, tests_version = NULL, lease_owner = NULL,
        lease_expires_at = NULL, started_at = NULL, judged_at = NULL
    WHERE problem_id = :problem_id
    RETURNING contest_id
    """
)


@dataclass(frozen=True)
class Rejudge:
    submissions: int
    contest_ids: frozenset[int]


def requeue_problem(db: Session, problem_id: int) -> Rejudge:
    """Queue every submission for the problem again, to be judged against its current tests."""
    rows = db.execute(REQUEUE_PROBLEM, {"problem_id": problem_id}).all()
    # One notification wakes every listening worker; each claims until the queue is empty.
    db.execute(text("SELECT pg_notify(:channel, '')"), {"channel": QUEUE_CHANNEL})
    db.commit()
    contest_ids = frozenset(row.contest_id for row in rows if row.contest_id is not None)
    return Rejudge(submissions=len(rows), contest_ids=contest_ids)
