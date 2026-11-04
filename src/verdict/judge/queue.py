from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Engine, TextClause, text

from verdict.core.verdicts import Language
from verdict.judge.runner import JudgeResult

# New submissions before rejudged ones (QueuePriority), and the oldest first within each, so a
# large rejudge fills idle time instead of making contestants wait. ix_submissions_pending
# holds pending rows in exactly this order.
CLAIM = text(
    """
    UPDATE submissions
    SET status = 'judging',
        attempt = attempt + 1,
        lease_owner = :worker_id,
        lease_expires_at = now() + make_interval(secs => :lease_seconds),
        started_at = now(),
        tests_version = (
            SELECT tests_version FROM problems WHERE problems.id = submissions.problem_id
        )
    WHERE id = (
        SELECT id FROM submissions
        WHERE status = 'queued'
           OR (status = 'judging' AND lease_expires_at < now())
        ORDER BY priority DESC, id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, attempt, problem_id, contest_id, language, source, tests_version
    """
)
# Every write after the claim matches id, attempt, owner and status 'judging', so a worker whose
# lease was taken over, given back or reset by a rejudge can never overwrite the row.
EXTEND_LEASE = text(
    """
    UPDATE submissions
    SET lease_expires_at = now() + make_interval(secs => :lease_seconds)
    WHERE id = :submission_id AND attempt = :attempt AND lease_owner = :worker_id
      AND status = 'judging'
    """
)
FINISH = text(
    """
    UPDATE submissions
    SET status = 'done', verdict = :verdict, failed_test = :failed_test, time_ms = :time_ms,
        memory_kb = :memory_kb, compile_output = :compile_output, judged_at = now()
    WHERE id = :submission_id AND attempt = :attempt AND lease_owner = :worker_id
      AND status = 'judging'
    """
)
MARK_FAILED = text(
    """
    UPDATE submissions
    SET status = 'failed', verdict = 'SE', judged_at = now()
    WHERE id = :submission_id AND attempt = :attempt AND lease_owner = :worker_id
      AND status = 'judging'
    """
)
RELEASE = text(
    """
    UPDATE submissions
    SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, started_at = NULL
    WHERE id = :submission_id AND attempt = :attempt AND lease_owner = :worker_id
      AND status = 'judging'
    """
)


@dataclass(frozen=True)
class Lease:
    submission_id: int
    attempt: int
    worker_id: str

    def fence(self) -> dict[str, object]:
        return {
            "submission_id": self.submission_id,
            "attempt": self.attempt,
            "worker_id": self.worker_id,
        }


@dataclass(frozen=True)
class ClaimedSubmission:
    lease: Lease
    problem_id: int
    contest_id: int | None
    language: Language
    source: str
    tests_version: int


def claim_submission(
    engine: Engine, worker_id: str, lease_seconds: float
) -> ClaimedSubmission | None:
    """Lease the oldest queued submission, or one whose lease expired; None if there is none."""
    with engine.begin() as connection:
        row = connection.execute(
            CLAIM, {"worker_id": worker_id, "lease_seconds": lease_seconds}
        ).one_or_none()
    if row is None:
        return None
    return ClaimedSubmission(
        lease=Lease(row.id, row.attempt, worker_id),
        problem_id=row.problem_id,
        contest_id=row.contest_id,
        language=Language(row.language),
        source=row.source,
        tests_version=row.tests_version,
    )


def extend_lease(engine: Engine, lease: Lease, lease_seconds: float) -> bool:
    """Push the lease's expiry forward; False means the lease is no longer this worker's."""
    return _fenced_update(engine, EXTEND_LEASE, lease, {"lease_seconds": lease_seconds})


def finish_submission(engine: Engine, lease: Lease, result: JudgeResult) -> bool:
    """Write the verdict; False means another worker owns the submission and the result is void."""
    values = {
        "verdict": result.verdict.value,
        "failed_test": result.failed_test,
        "time_ms": result.time_ms,
        "memory_kb": result.memory_kb,
        "compile_output": result.compile_output,
    }
    return _fenced_update(engine, FINISH, lease, values)


def mark_failed(engine: Engine, lease: Lease) -> bool:
    """Stop retrying a submission that keeps failing: status failed, verdict SE."""
    return _fenced_update(engine, MARK_FAILED, lease, {})


def release_lease(engine: Engine, lease: Lease) -> bool:
    """Put the submission back in the queue for another worker; the attempt count is kept."""
    return _fenced_update(engine, RELEASE, lease, {})


def _fenced_update(
    engine: Engine, statement: TextClause, lease: Lease, values: Mapping[str, object]
) -> bool:
    with engine.begin() as connection:
        result = connection.execute(statement, {**lease.fence(), **values})
    return result.rowcount == 1
