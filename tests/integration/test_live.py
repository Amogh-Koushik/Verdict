import asyncio
import json
import signal
import threading
import time
from collections.abc import Iterator
from datetime import timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.integration.contests import add_contest
from tests.integration.live_server import serve
from tests.integration.submissions import add_submissions
from verdict.core.accounts import create_user
from verdict.core.config import Settings
from verdict.core.events import publish_submission_changed, submission_channel
from verdict.core.models import Submission
from verdict.core.redis_client import create_async_redis
from verdict.core.verdicts import Verdict
from verdict.judge.queue import claim_submission, finish_submission
from verdict.judge.runner import JudgeResult
from verdict.web.live import end_streams_on_shutdown, event_stream
from verdict.web.main import create_app
from verdict.web.sessions import SESSION_COOKIE, create_session


@pytest.fixture
def server(app_settings: Settings, engine: Engine, redis: Redis) -> Iterator[str]:
    with serve(create_app(app_settings)) as base_url:
        yield base_url


def browser_for(redis: Redis, settings: Settings, base_url: str, user_id: int) -> httpx2.Client:
    session = create_session(redis, user_id, settings.session_ttl_seconds)
    return httpx2.Client(base_url=base_url, cookies={SESSION_COOKIE: session.token}, timeout=5)


def author_of(db: Session, submission_id: int) -> int:
    return db.scalars(select(Submission.user_id).where(Submission.id == submission_id)).one()


def stream_lines(response: httpx2.Response) -> Iterator[str]:
    return (line for line in response.iter_lines() if line)


def wait_for_subscribers(redis: Redis, channel: str, count: int) -> None:
    """Wait up to 5 s for `channel` to have `count` subscribers; streams close asynchronously."""
    deadline = time.monotonic() + 5
    while redis.pubsub_numsub(channel) != [(channel, count)] and time.monotonic() < deadline:
        time.sleep(0.05)


def test_an_open_stream_receives_the_event_when_a_verdict_lands(
    server: str, engine: Engine, db: Session, redis: Redis, app_settings: Settings
) -> None:
    [submission_id] = add_submissions(db, 1)
    with (
        browser_for(redis, app_settings, server, author_of(db, submission_id)) as browser,
        browser.stream("GET", f"/live/submissions/{submission_id}") as response,
    ):
        lines = stream_lines(response)
        assert next(lines) == "retry: 3000"
        # What the worker does once judging ends.
        claimed = claim_submission(engine, "worker-a", lease_seconds=60)
        assert claimed is not None
        assert finish_submission(
            engine, claimed.lease, JudgeResult(Verdict.ACCEPTED, None, 9, 3000)
        )
        publish_submission_changed(redis, submission_id, contest_id=None)

        assert json.loads(next(lines).removeprefix("data: ")) == {"submission_id": submission_id}

        fragment = browser.get(f"/submissions/{submission_id}/result")
    assert ">AC<" in fragment.text
    assert "data-pending" not in fragment.text


def test_the_stream_is_marked_uncacheable_and_unbuffered(
    server: str, db: Session, redis: Redis, app_settings: Settings
) -> None:
    [submission_id] = add_submissions(db, 1)
    with (
        browser_for(redis, app_settings, server, author_of(db, submission_id)) as browser,
        browser.stream("GET", f"/live/submissions/{submission_id}") as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"


def test_one_stream_relays_only_the_submissions_it_watches(
    server: str, db: Session, redis: Redis, app_settings: Settings
) -> None:
    first, second, unwatched = add_submissions(db, 3)
    with (
        browser_for(redis, app_settings, server, author_of(db, first)) as browser,
        browser.stream("GET", f"/live/submissions?ids={second},{first}") as response,
    ):
        lines = stream_lines(response)
        assert next(lines) == "retry: 3000"
        for submission_id in (unwatched, second, first):
            publish_submission_changed(redis, submission_id, contest_id=None)
        received = [json.loads(next(lines).removeprefix("data: ")) for _ in range(2)]
    assert received == [{"submission_id": second}, {"submission_id": first}]


def test_closing_the_stream_releases_its_redis_subscription(
    server: str, db: Session, redis: Redis, app_settings: Settings
) -> None:
    [submission_id] = add_submissions(db, 1)
    channel = submission_channel(submission_id)
    with (
        browser_for(redis, app_settings, server, author_of(db, submission_id)) as browser,
        browser.stream("GET", f"/live/submissions/{submission_id}") as response,
    ):
        # Kept in a variable: a discarded line iterator is closed, and closing it ends the stream.
        lines = stream_lines(response)
        next(lines)
        assert redis.pubsub_numsub(channel) == [(channel, 1)]
    wait_for_subscribers(redis, channel, 0)
    assert redis.pubsub_numsub(channel) == [(channel, 0)]


def test_only_the_author_or_an_admin_can_watch_a_submission(
    client: TestClient, db: Session, redis: Redis, app_settings: Settings
) -> None:
    [submission_id] = add_submissions(db, 1)
    assert client.get(f"/live/submissions/{submission_id}").status_code == 404
    stranger = create_user(db, "stranger", "a long enough password")
    session = create_session(redis, stranger.id, app_settings.session_ttl_seconds)
    client.cookies.set(SESSION_COOKIE, session.token)
    assert client.get(f"/live/submissions/{submission_id}").status_code == 404
    assert client.get(f"/live/submissions?ids={submission_id}").status_code == 404
    assert client.get(f"/submissions/{submission_id}/row").status_code == 404
    assert client.get(f"/submissions/{submission_id}/result").status_code == 404


def test_an_admin_can_watch_anyones_submission(
    server: str, db: Session, redis: Redis, app_settings: Settings
) -> None:
    [submission_id] = add_submissions(db, 1)
    admin = create_user(db, "admin", "a long enough password", is_admin=True)
    with (
        browser_for(redis, app_settings, server, admin.id) as browser,
        browser.stream("GET", f"/live/submissions/{submission_id}") as response,
    ):
        assert response.status_code == 200


@pytest.mark.parametrize(
    "ids",
    ["", "1,x", ",".join(str(n) for n in range(1, 52))],
    ids=["empty", "not a number", "51 ids"],
)
def test_a_malformed_list_of_submissions_is_refused(
    client: TestClient, db: Session, redis: Redis, app_settings: Settings, ids: str
) -> None:
    [submission_id] = add_submissions(db, 1)
    session = create_session(redis, author_of(db, submission_id), app_settings.session_ttl_seconds)
    client.cookies.set(SESSION_COOKIE, session.token)
    assert client.get(f"/live/submissions?ids={ids}").status_code == 400


def test_anyone_can_watch_a_started_contest_and_hears_when_a_verdict_lands(
    server: str, db: Session, redis: Redis
) -> None:
    contest = add_contest(db, starts_in=-timedelta(minutes=5))
    with (
        httpx2.Client(base_url=server, timeout=5) as anonymous,
        anonymous.stream("GET", f"/live/contests/{contest.id}") as response,
    ):
        lines = stream_lines(response)
        assert next(lines) == "retry: 3000"
        publish_submission_changed(redis, 41, contest.id)
        assert json.loads(next(lines).removeprefix("data: ")) == {"contest_id": contest.id}


def test_only_an_admin_can_watch_a_contest_before_it_starts(
    server: str, client: TestClient, db: Session, redis: Redis, app_settings: Settings
) -> None:
    contest = add_contest(db, starts_in=timedelta(minutes=5))
    assert client.get(f"/live/contests/{contest.id}").status_code == 404
    assert client.get(f"/live/contests/{contest.id + 1}").status_code == 404
    admin = create_user(db, "admin", "a long enough password", is_admin=True)
    with (
        browser_for(redis, app_settings, server, admin.id) as browser,
        browser.stream("GET", f"/live/contests/{contest.id}") as response,
    ):
        assert response.status_code == 200


def test_an_idle_stream_sends_keepalive_comments_and_still_delivers_events(
    redis: Redis, settings: Settings
) -> None:
    # Idles for 4 s, longer than the sync client's 3 s read timeout, to prove the stream survives.
    async def read_stream() -> list[str]:
        async_redis = create_async_redis(settings.redis_url)
        pubsub = async_redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(submission_channel(7))
        stream = event_stream(pubsub, keepalive_seconds=4, stopping=threading.Event())
        try:
            chunks = [await anext(stream)]
            started = time.monotonic()
            chunks.append(await anext(stream))
            assert time.monotonic() - started >= 3.9
            redis.publish(submission_channel(7), json.dumps({"submission_id": 7}))
            chunks.append(await anext(stream))
            return chunks
        finally:
            await stream.aclose()
            await async_redis.aclose()

    chunks = asyncio.run(read_stream())
    assert chunks == ["retry: 3000\n\n", ": keep-alive\n\n", 'data: {"submission_id": 7}\n\n']


def test_open_streams_end_cleanly_when_the_server_starts_stopping(
    app_settings: Settings, engine: Engine, db: Session, redis: Redis
) -> None:
    [submission_id] = add_submissions(db, 1)
    app = create_app(app_settings)
    with (
        serve(app) as base_url,
        browser_for(redis, app_settings, base_url, author_of(db, submission_id)) as browser,
        browser.stream("GET", f"/live/submissions/{submission_id}") as response,
    ):
        lines = stream_lines(response)
        assert next(lines) == "retry: 3000"
        started = time.monotonic()
        app.state.stopping.set()
        assert list(lines) == [], "the stream should end without sending anything else"
        assert time.monotonic() - started < 3


def test_a_stop_signal_sets_the_flag_and_still_reaches_the_previous_handler() -> None:
    received: list[int] = []
    originals = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    signal.signal(signal.SIGTERM, lambda signum, _frame: received.append(signum))
    try:
        stopping = threading.Event()
        end_streams_on_shutdown(stopping)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        # Called directly rather than raised, which works the same on every platform.
        handler(signal.SIGTERM, None)
    finally:
        for sig, original in originals.items():
            signal.signal(sig, original)
    assert stopping.is_set()
    assert received == [signal.SIGTERM]
