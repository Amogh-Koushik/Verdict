import logging
import signal
import threading
import time
from collections.abc import AsyncIterator, Callable
from types import FrameType
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from verdict.core.events import submission_channel
from verdict.core.models import Submission
from verdict.web.deps import get_viewer

logger = logging.getLogger(__name__)

router = APIRouter()

KEEPALIVE_SECONDS = 15.0
# How soon an open stream notices that the server is shutting down.
STOP_CHECK_SECONDS = 1.0
RECONNECT_MILLISECONDS = 3000
MAX_WATCHED_SUBMISSIONS = 50
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/live/submissions/{submission_id}")
async def live_submission(request: Request, submission_id: int) -> StreamingResponse:
    return await _stream_submissions(request, [submission_id])


@router.get("/live/submissions")
async def live_submissions(request: Request, ids: str = "") -> StreamingResponse:
    """One stream for several submissions, so a list page needs a single connection."""
    try:
        submission_ids = sorted({int(part) for part in ids.split(",")})
    except ValueError:
        raise HTTPException(status_code=400, detail="ids must be submission numbers") from None
    if len(submission_ids) > MAX_WATCHED_SUBMISSIONS:
        raise HTTPException(status_code=400, detail="too many submissions in one stream")
    return await _stream_submissions(request, submission_ids)


def end_streams_on_shutdown(stopping: threading.Event) -> None:
    """Set `stopping` on SIGTERM or SIGINT, then hand the signal to Uvicorn's own handler."""
    # Uvicorn waits for open responses before it stops, and never tells the app it is stopping.
    # Without this, every open stream would be cancelled, and logged as an error, at shutdown.
    if threading.current_thread() is not threading.main_thread():
        return  # Only the main thread receives signals, so a server run in a thread has none.
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop_signal, _set_then(stopping, signal.getsignal(stop_signal)))


async def event_stream(
    pubsub: PubSub, keepalive_seconds: float, stopping: threading.Event
) -> AsyncIterator[str]:
    """Relay each published message as an SSE event, with a comment line when idle."""
    try:
        yield f"retry: {RECONNECT_MILLISECONDS}\n\n"
        last_sent = time.monotonic()
        while not stopping.is_set():
            until_keepalive = last_sent + keepalive_seconds - time.monotonic()
            wait = max(0.0, min(until_keepalive, STOP_CHECK_SECONDS))
            message = await pubsub.get_message(timeout=wait)
            if message is not None:
                yield f"data: {message['data']}\n\n"
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= keepalive_seconds:
                yield ": keep-alive\n\n"
                last_sent = time.monotonic()
    except RedisError:
        # Ending the stream makes the browser reconnect, and it re-fetches on reconnect.
        logger.warning("live stream lost its Redis connection", exc_info=True)
    finally:
        await _close(pubsub)


async def _stream_submissions(request: Request, submission_ids: list[int]) -> StreamingResponse:
    if not await run_in_threadpool(_may_watch, request, submission_ids):
        raise HTTPException(status_code=404)
    redis: AsyncRedis = request.app.state.async_redis
    pubsub = redis.pubsub(ignore_subscribe_messages=True)
    try:
        # Subscribed before the headers go out: the browser treats them as "listening now".
        await pubsub.subscribe(*(submission_channel(sid) for sid in submission_ids))
    except RedisError:
        await _close(pubsub)
        logger.warning("live stream could not subscribe", exc_info=True)
        raise HTTPException(status_code=503, detail="Live updates are unavailable.") from None
    return StreamingResponse(
        event_stream(pubsub, KEEPALIVE_SECONDS, request.app.state.stopping),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


def _set_then(
    stopping: threading.Event, previous: Callable[[int, FrameType | None], Any] | int | None
) -> Callable[[int, FrameType | None], None]:
    def handle(signum: int, frame: FrameType | None) -> None:
        stopping.set()
        if callable(previous):
            previous(signum, frame)

    return handle


async def _close(pubsub: PubSub) -> None:
    # redis-py leaves aclose unannotated; reset(), the typed alias, is deprecated.
    await pubsub.aclose()  # type: ignore[no-untyped-call]


def _may_watch(request: Request, submission_ids: list[int]) -> bool:
    # Its own short session: a request-scoped one would hold a pooled connection for the
    # whole life of the stream.
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as db:
        user = get_viewer(request, request.app.state.redis, db).user
        if user is None or not submission_ids:
            return False
        owners = db.scalars(
            select(Submission.user_id).where(Submission.id.in_(submission_ids))
        ).all()
    if len(owners) != len(submission_ids):
        return False
    return user.is_admin or all(owner == user.id for owner in owners)
