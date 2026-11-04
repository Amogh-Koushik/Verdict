import json
from dataclasses import replace
from datetime import timedelta

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from tests.integration.contests import add_contest, add_contest_problem, add_contest_submission
from tests.integration.events import next_event
from tests.integration.forms import csrf_token_from, log_in_as_admin
from tests.integration.submissions import add_submissions
from verdict.core.accounts import create_user
from verdict.core.config import Settings
from verdict.core.events import QUEUE_CHANNEL, contest_channel, scoreboard_key
from verdict.core.models import Problem, Submission
from verdict.core.verdicts import Language, SubmissionStatus, Verdict
from verdict.judge.queue import claim_submission, extend_lease, finish_submission
from verdict.judge.runner import JudgeResult
from verdict.judge.worker import claim_next_submission
from verdict.web.rejudge import requeue_problem
from verdict.web.scoreboard_cache import contest_scoreboard

ACCEPTED = JudgeResult(Verdict.ACCEPTED, None, 12, 3000)
WRONG = JudgeResult(Verdict.WRONG_ANSWER, 2, 15, 3100)


@pytest.fixture
def admin(client: TestClient, db: Session) -> TestClient:
    log_in_as_admin(client, db)
    return client


def post_rejudge(client: TestClient, problem_id: int) -> httpx2.Response:
    csrf_token = csrf_token_from(client.get(f"/admin/problems/{problem_id}/tests"))
    return client.post(
        f"/admin/problems/{problem_id}/rejudge",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )


def judge_all(engine: Engine, worker: str, result: JudgeResult) -> None:
    while (claimed := claim_submission(engine, worker, lease_seconds=60)) is not None:
        assert finish_submission(engine, claimed.lease, result)


def problem_of(db: Session, submission_id: int) -> Problem:
    return db.get_one(Problem, db.get_one(Submission, submission_id).problem_id)


def test_rejudge_queues_every_submission_of_the_problem_and_clears_its_result(
    admin: TestClient, db: Session, engine: Engine
) -> None:
    first, second, third = add_submissions(db, 3)
    [elsewhere] = add_submissions(db, 1)
    judge_all(engine, "worker-a", WRONG)
    problem = problem_of(db, first)
    submitted_at = db.get_one(Submission, first).created_at

    response = post_rejudge(admin, problem.id)

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/problems/{problem.id}/tests?rejudged=3"
    db.expire_all()
    for submission_id in (first, second, third):
        submission = db.get_one(Submission, submission_id)
        assert submission.status is SubmissionStatus.QUEUED
        assert submission.attempt == 0
        assert (submission.verdict, submission.failed_test, submission.time_ms) == (
            None,
            None,
            None,
        )
        assert (submission.memory_kb, submission.tests_version) == (None, None)
        assert (submission.lease_owner, submission.started_at, submission.judged_at) == (
            None,
            None,
            None,
        )
    assert db.get_one(Submission, first).created_at == submitted_at
    untouched = db.get_one(Submission, elsewhere)
    assert (untouched.status, untouched.verdict) == (SubmissionStatus.DONE, Verdict.WRONG_ANSWER)
    page = admin.get(response.headers["location"]).text
    assert "3 submissions went back in the queue, to be judged against version 1." in page


def test_rejudge_wakes_the_workers(db: Session, engine: Engine) -> None:
    [submission_id] = add_submissions(db, 1)
    conninfo = make_url(engine.url).set(drivername="postgresql").render_as_string(False)
    with psycopg.connect(conninfo, autocommit=True) as listener:
        listener.execute(f"LISTEN {QUEUE_CHANNEL}")
        requeue_problem(db, problem_of(db, submission_id).id)
        notifications = list(listener.notifies(timeout=2, stop_after=1))
    assert len(notifications) == 1


def test_a_rejudged_submission_is_judged_against_the_new_tests(db: Session, engine: Engine) -> None:
    [submission_id] = add_submissions(db, 1)
    judge_all(engine, "worker-a", WRONG)
    problem = problem_of(db, submission_id)
    problem.tests_version = 2
    db.commit()

    requeue_problem(db, problem.id)

    claimed = claim_submission(engine, "worker-a", lease_seconds=60)
    assert claimed is not None
    assert claimed.tests_version == 2


def test_the_old_runs_result_is_thrown_away_after_a_rejudge(db: Session, engine: Engine) -> None:
    [submission_id] = add_submissions(db, 1)
    old_run = claim_submission(engine, "worker-a", lease_seconds=60)
    assert old_run is not None

    requeue_problem(db, problem_of(db, submission_id).id)

    assert not extend_lease(engine, old_run.lease, lease_seconds=60)
    assert not finish_submission(engine, old_run.lease, ACCEPTED)
    db.expire_all()
    assert db.get_one(Submission, submission_id).status is SubmissionStatus.QUEUED


def test_the_old_run_cannot_overwrite_the_new_run_even_with_the_same_attempt_number(
    db: Session, engine: Engine
) -> None:
    [submission_id] = add_submissions(db, 1)
    old_run = claim_submission(engine, "worker-a", lease_seconds=60)
    assert old_run is not None
    requeue_problem(db, problem_of(db, submission_id).id)
    new_run = claim_submission(engine, "worker-b", lease_seconds=60)
    assert new_run is not None
    assert new_run.lease.attempt == old_run.lease.attempt == 1

    assert not finish_submission(engine, old_run.lease, ACCEPTED)
    assert finish_submission(engine, new_run.lease, WRONG)

    db.expire_all()
    submission = db.get_one(Submission, submission_id)
    assert (submission.verdict, submission.lease_owner) == (Verdict.WRONG_ANSWER, "worker-b")


def test_rejudging_many_times_never_uses_up_a_submissions_attempts(
    db: Session, engine: Engine, redis: Redis, settings: Settings
) -> None:
    [submission_id] = add_submissions(db, 1)
    problem_id = problem_of(db, submission_id).id
    limits = replace(settings, max_attempts=3)
    for _ in range(5):
        claimed = claim_next_submission(engine, redis, "worker-a", limits)
        assert claimed is not None, "the submission was marked failed instead of judged"
        assert finish_submission(engine, claimed.lease, ACCEPTED)
        requeue_problem(db, problem_id)
    db.expire_all()
    assert db.get_one(Submission, submission_id).verdict is None


def test_rejudge_refreshes_the_scoreboards_of_contests_with_those_submissions(
    admin: TestClient, db: Session, redis: Redis
) -> None:
    contest = add_contest(db, starts_in=-timedelta(minutes=30))
    problem = add_contest_problem(db, contest, "A", "sum")
    alice = create_user(db, "alice", "a long enough password")
    add_contest_submission(db, contest, alice, problem, minute=5, verdict=Verdict.ACCEPTED)
    assert contest_scoreboard(db, redis, contest).rows[0].cells[0].mark == "+"
    events = redis.pubsub(ignore_subscribe_messages=True)
    events.subscribe(contest_channel(contest.id))

    post_rejudge(admin, problem.id)

    message = next_event(events)
    assert message is not None
    assert json.loads(message["data"]) == {"contest_id": contest.id}
    assert not redis.exists(scoreboard_key(contest.id))
    assert contest_scoreboard(db, redis, contest).rows[0].cells[0].mark == "?"
    events.close()


def test_a_problem_without_tests_cannot_be_rejudged(admin: TestClient, db: Session) -> None:
    problem = Problem(slug="draft", title="Draft")
    db.add(problem)
    db.flush()
    user = create_user(db, "alice", "a long enough password")
    db.add(Submission(user_id=user.id, problem_id=problem.id, language=Language.PYTHON, source="1"))
    db.commit()
    assert "Rejudge" not in admin.get(f"/admin/problems/{problem.id}/tests").text

    response = post_rejudge(admin, problem.id)

    assert response.status_code == 400
    assert "Upload tests before rejudging." in response.text


def test_the_tests_page_offers_a_rejudge_only_when_there_is_something_to_rejudge(
    admin: TestClient, db: Session
) -> None:
    problem = Problem(slug="sum", title="Sum", tests_version=1)
    db.add(problem)
    db.commit()
    assert "Rejudge" not in admin.get(f"/admin/problems/{problem.id}/tests").text
    user = create_user(db, "alice", "a long enough password")
    db.add(Submission(user_id=user.id, problem_id=problem.id, language=Language.PYTHON, source="1"))
    db.commit()
    assert "Rejudge 1 submission</button>" in admin.get(f"/admin/problems/{problem.id}/tests").text


def test_rejudge_counts_only_the_problems_submissions(db: Session) -> None:
    first, _ = add_submissions(db, 2)
    add_submissions(db, 1)
    rejudge = requeue_problem(db, problem_of(db, first).id)
    assert (rejudge.submissions, rejudge.contest_ids) == (2, frozenset())
