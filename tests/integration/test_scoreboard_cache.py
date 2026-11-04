import json
from datetime import timedelta

import pytest
from redis import Redis
from sqlalchemy.orm import Session

from tests.integration.contests import add_contest, add_contest_problem, add_contest_submission
from tests.integration.events import next_event
from verdict.core.accounts import create_user
from verdict.core.events import (
    contest_channel,
    publish_contest_changed,
    publish_submission_changed,
    scoreboard_generation_key,
    scoreboard_key,
)
from verdict.core.models import Contest, Problem, User
from verdict.core.redis_client import create_redis
from verdict.core.verdicts import Verdict
from verdict.web import scoreboard_cache
from verdict.web.scoreboard import Scoreboard
from verdict.web.scoreboard_cache import contest_scoreboard


@pytest.fixture
def contest(db: Session) -> Contest:
    return add_contest(db, starts_in=-timedelta(minutes=30))


@pytest.fixture
def problem(db: Session, contest: Contest) -> Problem:
    return add_contest_problem(db, contest, "A", "sum")


@pytest.fixture
def alice(db: Session) -> User:
    return create_user(db, "alice", "a long enough password")


def test_the_scoreboard_is_cached_until_a_verdict_for_the_contest_lands(
    db: Session, redis: Redis, contest: Contest, problem: Problem, alice: User
) -> None:
    assert contest_scoreboard(db, redis, contest).rows == ()
    assert redis.exists(scoreboard_key(contest.id))
    accepted = add_contest_submission(
        db, contest, alice, problem, minute=7, verdict=Verdict.ACCEPTED
    )
    assert contest_scoreboard(db, redis, contest).rows == (), "served from the cache"

    publish_submission_changed(redis, accepted.id, contest.id)

    assert not redis.exists(scoreboard_key(contest.id))
    [row] = contest_scoreboard(db, redis, contest).rows
    assert (row.username, row.solved, row.penalty) == ("alice", 1, 7)


def test_administrators_are_left_off_the_scoreboard(
    db: Session, redis: Redis, contest: Contest, problem: Problem, alice: User
) -> None:
    organiser = create_user(db, "organiser", "a long enough password", is_admin=True)
    add_contest_submission(db, contest, organiser, problem, minute=1, verdict=Verdict.ACCEPTED)
    add_contest_submission(db, contest, alice, problem, minute=9, verdict=Verdict.WRONG_ANSWER)
    [row] = contest_scoreboard(db, redis, contest).rows
    assert (row.rank, row.username, row.cells[0].mark) == (1, "alice", "-1")


def test_a_cached_scoreboard_is_the_same_as_a_freshly_computed_one(
    db: Session, redis: Redis, contest: Contest, problem: Problem, alice: User
) -> None:
    add_contest_submission(db, contest, alice, problem, minute=3, verdict=Verdict.WRONG_ANSWER)
    add_contest_submission(db, contest, alice, problem, minute=9, verdict=None)
    computed = contest_scoreboard(db, redis, contest)
    cached = contest_scoreboard(db, redis, contest)
    assert cached == computed
    assert cached.rows[0].cells[0].mark == "?"


def test_a_scoreboard_computed_before_a_change_is_not_served_after_it(
    db: Session,
    redis: Redis,
    contest: Contest,
    problem: Problem,
    alice: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compute = scoreboard_cache.compute_scoreboard

    def verdict_lands_while_computing(session: Session, watched: Contest) -> Scoreboard:
        # The worker's verdict and invalidation land between this read and the cache write.
        before_the_verdict = compute(session, watched)
        accepted = add_contest_submission(
            db, contest, alice, problem, minute=7, verdict=Verdict.ACCEPTED
        )
        publish_submission_changed(redis, accepted.id, contest.id)
        return before_the_verdict

    monkeypatch.setattr(scoreboard_cache, "compute_scoreboard", verdict_lands_while_computing)
    assert contest_scoreboard(db, redis, contest).rows == ()
    monkeypatch.undo()

    [row] = contest_scoreboard(db, redis, contest).rows
    assert row.solved == 1


def test_a_change_to_the_contest_itself_drops_its_cached_scoreboard(
    db: Session, redis: Redis, contest: Contest
) -> None:
    contest_scoreboard(db, redis, contest)
    events = redis.pubsub(ignore_subscribe_messages=True)
    events.subscribe(contest_channel(contest.id))

    publish_contest_changed(redis, contest.id)

    message = next_event(events)
    assert message is not None
    assert json.loads(message["data"]) == {"contest_id": contest.id}
    assert not redis.exists(scoreboard_key(contest.id))
    assert redis.get(scoreboard_generation_key(contest.id)) == "1"
    events.close()


def test_the_scoreboard_comes_from_postgres_when_redis_is_unreachable(
    db: Session, contest: Contest, problem: Problem, alice: User
) -> None:
    add_contest_submission(db, contest, alice, problem, minute=7, verdict=Verdict.ACCEPTED)
    unreachable = create_redis("redis://127.0.0.1:1/0")
    [row] = contest_scoreboard(db, unreachable, contest).rows
    assert row.solved == 1
    unreachable.close()
