import hashlib
import time
from collections.abc import Callable

from docker import DockerClient
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from tests.integration.forms import csrf_token_from, register
from tests.sandbox.conftest import (
    SUM_TESTS,
    RunningWorker,
    sandbox_containers,
    wait_until,
)
from verdict.core.accounts import create_user
from verdict.core.config import Settings
from verdict.core.models import Problem, ProblemTest, Submission, problem_tests_dir
from verdict.core.verdicts import Language, SubmissionStatus, Verdict

SUM_SOLUTION = "a, b = map(int, input().split())\nprint(a + b)\n"


def add_problem(db: Session, settings: Settings, time_limit_ms: int = 1000) -> Problem:
    """A public sum problem whose tests are stored where the worker reads them."""
    problem = Problem(
        slug="sum", title="Sum", is_public=True, tests_version=1, time_limit_ms=time_limit_ms
    )
    db.add(problem)
    db.flush()
    tests_dir = problem_tests_dir(settings.problems_dir, problem.id, 1)
    tests_dir.mkdir(parents=True)
    for idx, (given, expected) in enumerate(SUM_TESTS, start=1):
        (tests_dir / f"{idx}.in").write_text(given)
        (tests_dir / f"{idx}.out").write_text(expected)
        db.add(
            ProblemTest(
                problem_id=problem.id,
                version=1,
                idx=idx,
                input_sha256=hashlib.sha256(given.encode()).hexdigest(),
                output_sha256=hashlib.sha256(expected.encode()).hexdigest(),
            )
        )
    db.commit()
    return problem


def queue(db: Session, problem: Problem, source: str) -> int:
    user = create_user(db, f"user{time.monotonic_ns()}", "a long enough password")
    submission = Submission(
        user_id=user.id, problem_id=problem.id, language=Language.PYTHON, source=source
    )
    db.add(submission)
    db.commit()
    return submission.id


def status_of(db: Session, submission_id: int) -> Submission:
    db.expire_all()
    return db.get_one(Submission, submission_id)


def test_an_accepted_solution_submitted_over_http_is_judged_within_ten_seconds(
    client: TestClient,
    db: Session,
    app_settings: Settings,
    start_worker: Callable[..., RunningWorker],
) -> None:
    add_problem(db, app_settings)
    start_worker()
    register(client, "alice", "a long enough password")
    csrf_token = csrf_token_from(client.get("/problems/sum"))

    submitted_at = time.monotonic()
    response = client.post(
        "/problems/sum/submit",
        data={"csrf_token": csrf_token, "language": "python", "source": SUM_SOLUTION},
        follow_redirects=False,
    )
    location = response.headers["location"]

    assert wait_until(lambda: ">AC<" in client.get(location).text, timeout_seconds=10)
    assert time.monotonic() - submitted_at < 10


def test_stopping_mid_judge_finishes_a_submission_that_ends_within_the_grace_period(
    db: Session, app_settings: Settings, start_worker: Callable[..., RunningWorker]
) -> None:
    problem = add_problem(db, app_settings)
    slow = "import time\ntime.sleep(1.5)\n" + SUM_SOLUTION
    submission_id = queue(db, problem, slow)
    worker = start_worker()
    assert wait_until(lambda: status_of(db, submission_id).status is SubmissionStatus.JUDGING, 10)

    worker.stop()

    assert worker.join(30)
    row = status_of(db, submission_id)
    assert row.status is SubmissionStatus.DONE
    assert row.verdict is Verdict.ACCEPTED


def test_stopping_mid_judge_gives_a_long_submission_back_to_the_queue(
    db: Session,
    app_settings: Settings,
    start_worker: Callable[..., RunningWorker],
    docker_client: DockerClient,
) -> None:
    problem = add_problem(db, app_settings, time_limit_ms=10_000)
    submission_id = queue(db, problem, "import time\ntime.sleep(60)\n")
    worker = start_worker(shutdown_grace_seconds=1)
    assert wait_until(lambda: status_of(db, submission_id).status is SubmissionStatus.JUDGING, 10)
    assert wait_until(lambda: sandbox_containers(docker_client) != [], 10)

    stopped_at = time.monotonic()
    worker.stop()

    assert worker.join(30)
    assert time.monotonic() - stopped_at < 10
    row = status_of(db, submission_id)
    assert row.status is SubmissionStatus.QUEUED
    assert row.lease_owner is None
    assert row.verdict is None
    assert sandbox_containers(docker_client) == []


def test_a_worker_that_loses_its_lease_stops_judging_and_discards_its_result(
    db: Session,
    app_settings: Settings,
    start_worker: Callable[..., RunningWorker],
    docker_client: DockerClient,
) -> None:
    problem = add_problem(db, app_settings, time_limit_ms=10_000)
    submission_id = queue(db, problem, "import time\ntime.sleep(60)\n")
    start_worker(heartbeat_seconds=1)
    assert wait_until(lambda: sandbox_containers(docker_client) != [], 10)

    # Another worker takes over, as it would after this one stalled past its lease.
    db.execute(
        update(Submission)
        .where(Submission.id == submission_id)
        .values(attempt=Submission.attempt + 1, lease_owner="other-worker")
    )
    db.commit()

    assert wait_until(lambda: sandbox_containers(docker_client) == [], 10)
    row = status_of(db, submission_id)
    assert row.status is SubmissionStatus.JUDGING
    assert row.lease_owner == "other-worker"
    assert row.verdict is None
