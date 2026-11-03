import threading
import time

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.integration.submissions import add_submissions
from verdict.core.models import Submission
from verdict.core.verdicts import SubmissionStatus, Verdict
from verdict.judge.queue import (
    claim_submission,
    extend_lease,
    finish_submission,
    mark_failed,
    release_lease,
)
from verdict.judge.runner import JudgeResult

ACCEPTED = JudgeResult(Verdict.ACCEPTED, time_ms=12, memory_kb=3400)


def test_claim_takes_the_oldest_queued_submission_and_leases_it(
    engine: Engine, db: Session
) -> None:
    first, _ = add_submissions(db, 2)
    claimed = claim_submission(engine, "worker-a", lease_seconds=60)
    assert claimed is not None
    assert claimed.lease.submission_id == first
    assert claimed.lease.attempt == 1
    assert claimed.tests_version == 1
    row = db.get_one(Submission, first)
    db.refresh(row)
    assert row.status is SubmissionStatus.JUDGING
    assert row.lease_owner == "worker-a"
    assert row.started_at is not None
    assert row.lease_expires_at is not None
    assert row.tests_version == 1


def test_claim_returns_none_when_nothing_is_waiting(engine: Engine) -> None:
    assert claim_submission(engine, "worker-a", lease_seconds=60) is None


def test_200_submissions_claimed_by_8_concurrent_claimers_are_each_claimed_once(
    engine: Engine, db: Session
) -> None:
    submission_ids = add_submissions(db, 200)
    claims: list[list[int]] = [[] for _ in range(8)]
    start = threading.Barrier(8)

    def claim_until_empty(worker: int) -> None:
        start.wait()
        while (claimed := claim_submission(engine, f"worker-{worker}", 60)) is not None:
            claims[worker].append(claimed.lease.submission_id)

    threads = [threading.Thread(target=claim_until_empty, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    claimed_ids = [submission_id for worker_claims in claims for submission_id in worker_claims]
    assert sorted(claimed_ids) == submission_ids
    assert sum(1 for worker_claims in claims if worker_claims) > 1, "claims were not concurrent"


def test_an_expired_lease_is_reclaimed_by_another_worker(engine: Engine, db: Session) -> None:
    [submission_id] = add_submissions(db, 1)
    first = claim_submission(engine, "stalled-worker", lease_seconds=1)
    assert first is not None
    assert claim_submission(engine, "other-worker", lease_seconds=60) is None
    time.sleep(1.2)
    second = claim_submission(engine, "other-worker", lease_seconds=60)
    assert second is not None
    assert second.lease.submission_id == submission_id
    assert second.lease.attempt == 2


def test_a_stale_workers_finish_is_rejected_by_fencing(engine: Engine, db: Session) -> None:
    [submission_id] = add_submissions(db, 1)
    stale = claim_submission(engine, "stalled-worker", lease_seconds=1)
    time.sleep(1.2)
    current = claim_submission(engine, "other-worker", lease_seconds=60)
    assert stale is not None
    assert current is not None
    assert not finish_submission(engine, stale.lease, JudgeResult(Verdict.WRONG_ANSWER, 1))
    assert not extend_lease(engine, stale.lease, lease_seconds=60)
    assert finish_submission(engine, current.lease, ACCEPTED)
    row = db.get_one(Submission, submission_id)
    db.refresh(row)
    assert row.status is SubmissionStatus.DONE
    assert row.verdict is Verdict.ACCEPTED
    assert (row.time_ms, row.memory_kb, row.failed_test) == (12, 3400, None)
    assert row.judged_at is not None


def test_a_finished_submission_cannot_be_finished_again(engine: Engine, db: Session) -> None:
    add_submissions(db, 1)
    claimed = claim_submission(engine, "worker-a", lease_seconds=60)
    assert claimed is not None
    assert finish_submission(engine, claimed.lease, ACCEPTED)
    assert not finish_submission(engine, claimed.lease, JudgeResult(Verdict.WRONG_ANSWER, 1))


def test_extending_a_held_lease_moves_its_expiry_forward(engine: Engine, db: Session) -> None:
    [submission_id] = add_submissions(db, 1)
    claimed = claim_submission(engine, "worker-a", lease_seconds=1)
    assert claimed is not None
    assert extend_lease(engine, claimed.lease, lease_seconds=60)
    time.sleep(1.2)
    assert claim_submission(engine, "other-worker", lease_seconds=60) is None
    row = db.get_one(Submission, submission_id)
    assert row.lease_owner == "worker-a"


def test_a_released_lease_puts_the_submission_back_with_its_attempt_count(
    engine: Engine, db: Session
) -> None:
    [submission_id] = add_submissions(db, 1)
    leaving = claim_submission(engine, "stopping-worker", lease_seconds=60)
    assert leaving is not None
    assert release_lease(engine, leaving.lease)
    row = db.get_one(Submission, submission_id)
    db.refresh(row)
    assert row.status is SubmissionStatus.QUEUED
    assert row.lease_owner is None
    assert row.started_at is None
    taken_over = claim_submission(engine, "other-worker", lease_seconds=60)
    assert taken_over is not None
    assert taken_over.lease.attempt == 2
    assert not finish_submission(engine, leaving.lease, ACCEPTED)


def test_marking_failed_records_a_system_error(engine: Engine, db: Session) -> None:
    [submission_id] = add_submissions(db, 1)
    claimed = claim_submission(engine, "worker-a", lease_seconds=60)
    assert claimed is not None
    assert mark_failed(engine, claimed.lease)
    row = db.get_one(Submission, submission_id)
    db.refresh(row)
    assert row.status is SubmissionStatus.FAILED
    assert row.verdict is Verdict.SYSTEM_ERROR
