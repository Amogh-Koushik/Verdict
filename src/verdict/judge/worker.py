import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType

import docker
from docker import DockerClient
from docker.errors import ImageNotFound
from redis import Redis
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from verdict.core.config import Settings, load_settings
from verdict.core.db import create_db_engine
from verdict.core.events import publish_submission_changed
from verdict.core.logs import configure_logging
from verdict.core.models import Problem, ProblemTest, problem_tests_dir
from verdict.core.redis_client import create_redis
from verdict.core.verdicts import Verdict
from verdict.judge.listener import QueueListener
from verdict.judge.queue import (
    ClaimedSubmission,
    Lease,
    claim_submission,
    extend_lease,
    finish_submission,
    mark_failed,
    release_lease,
)
from verdict.judge.runner import RUNNER_IMAGES, JudgeJob, JudgeResult, judge
from verdict.judge.sandbox import kill_sandbox, remove_leftover_sandboxes

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_SECONDS = 10.0
GUARD_TICK_SECONDS = 0.5


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


@dataclass(frozen=True)
class WorkerContext:
    settings: Settings
    engine: Engine
    redis: Redis
    docker: DockerClient
    worker_id: str
    stop: threading.Event
    shutdown_grace_seconds: float = SHUTDOWN_GRACE_SECONDS


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    stop = threading.Event()

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("stop requested", extra={"signal": signal.Signals(signum).name})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    context = WorkerContext(
        settings=settings,
        engine=create_db_engine(settings.database_url),
        redis=create_redis(settings.redis_url),
        docker=docker.from_env(),
        worker_id=worker_id(),
        stop=stop,
    )
    missing = missing_runner_images(context.docker)
    if missing:
        logger.error("runner images missing; run make runners", extra={"images": missing})
        raise SystemExit(1)
    removed = remove_leftover_sandboxes(context.docker, context.worker_id)
    logger.info("worker started", extra={"worker_id": context.worker_id, "removed": removed})
    listener = QueueListener(settings.database_url)
    try:
        run_worker(context, listener.wait)
    finally:
        listener.close()
        context.redis.close()
        context.engine.dispose()
    logger.info("worker stopped", extra={"worker_id": context.worker_id})


def missing_runner_images(client: DockerClient) -> list[str]:
    missing = []
    for image in RUNNER_IMAGES.values():
        try:
            client.images.get(image)
        except ImageNotFound:
            missing.append(image)
    return missing


def run_worker(context: WorkerContext, wait_for_work: Callable[[float], None]) -> None:
    """Judge submissions one at a time until `context.stop` is set."""
    while not context.stop.is_set():
        try:
            judged = judge_next_submission(context)
        except Exception:
            # Postgres or Docker being briefly unreachable must not end the worker.
            logger.exception("worker loop failed", extra={"worker_id": context.worker_id})
            judged = False
        if not judged and not context.stop.is_set():
            wait_for_work(context.settings.poll_seconds)


def judge_next_submission(context: WorkerContext) -> bool:
    """Claim, judge and record one submission; False if the queue was empty."""
    claimed = claim_next_submission(
        context.engine, context.redis, context.worker_id, context.settings
    )
    if claimed is None:
        return False
    lease = claimed.lease
    publish_submission_changed(context.redis, lease.submission_id, claimed.contest_id)
    result = _judge_while_holding(context, claimed)
    log_fields = {"submission_id": lease.submission_id, "attempt": lease.attempt}
    if finish_submission(context.engine, lease, result):
        logger.info("submission judged", extra={**log_fields, "verdict": result.verdict.value})
    else:
        logger.warning("result discarded: the lease is no longer held", extra=log_fields)
    # Either way the row changed: judged, put back in the queue, or taken over.
    publish_submission_changed(context.redis, lease.submission_id, claimed.contest_id)
    return True


def claim_next_submission(
    engine: Engine, redis: Redis, worker: str, settings: Settings
) -> ClaimedSubmission | None:
    """Claim the next submission, marking any that has used up its attempts as failed."""
    while True:
        claimed = claim_submission(engine, worker, settings.lease_seconds)
        if claimed is None or claimed.lease.attempt <= settings.max_attempts:
            return claimed
        lease = claimed.lease
        if mark_failed(engine, lease):
            logger.error(
                "submission failed on every attempt",
                extra={"submission_id": lease.submission_id, "attempts": lease.attempt - 1},
            )
            publish_submission_changed(redis, lease.submission_id, claimed.contest_id)


def _judge_while_holding(context: WorkerContext, claimed: ClaimedSubmission) -> JudgeResult:
    job = _judge_job(context, claimed)
    if job is None:
        return JudgeResult(Verdict.SYSTEM_ERROR)
    finished = threading.Event()
    abandoned = threading.Event()
    guard = threading.Thread(
        target=_guard_lease, args=(context, claimed.lease, finished, abandoned), daemon=True
    )
    guard.start()
    try:
        return judge(context.docker, job, context.worker_id, abandoned)
    finally:
        finished.set()
        guard.join()


def _judge_job(context: WorkerContext, claimed: ClaimedSubmission) -> JudgeJob | None:
    settings = context.settings
    with Session(context.engine) as db:
        problem = db.get_one(Problem, claimed.problem_id)
        test_count = db.scalar(
            select(func.count())
            .select_from(ProblemTest)
            .where(
                ProblemTest.problem_id == problem.id,
                ProblemTest.version == claimed.tests_version,
            )
        )
    if not test_count:
        # Accepting against zero tests would be wrong, so this is the system's fault, not theirs.
        logger.error(
            "problem has no tests to judge against",
            extra={"submission_id": claimed.lease.submission_id, "problem_id": problem.id},
        )
        return None
    return JudgeJob(
        language=claimed.language,
        source=claimed.source,
        time_limit_ms=problem.time_limit_ms_for(claimed.language, settings.python_time_multiplier),
        memory_limit_mb=problem.memory_limit_mb,
        tests_dir=problem_tests_dir(settings.problems_dir, problem.id, claimed.tests_version),
        test_count=test_count,
        submission=str(claimed.lease.submission_id),
    )


def _guard_lease(
    context: WorkerContext, lease: Lease, finished: threading.Event, abandoned: threading.Event
) -> None:
    if _hold_lease_until_finished(context, lease, finished):
        return
    abandoned.set()
    kill_sandbox(context.docker, str(lease.submission_id), context.worker_id)


def _hold_lease_until_finished(
    context: WorkerContext, lease: Lease, finished: threading.Event
) -> bool:
    """Heartbeat until judging ends (True), or the lease is lost or shutdown runs out (False)."""
    heartbeat_seconds = context.settings.heartbeat_seconds
    next_heartbeat = time.monotonic() + heartbeat_seconds
    give_up_at: float | None = None
    while not finished.wait(GUARD_TICK_SECONDS):
        now = time.monotonic()
        if give_up_at is None and context.stop.is_set():
            give_up_at = now + context.shutdown_grace_seconds
        if give_up_at is not None and now >= give_up_at:
            _give_back(context, lease)
            return False
        if now >= next_heartbeat:
            next_heartbeat = now + heartbeat_seconds
            if not _heartbeat(context, lease):
                return False
    return True


def _heartbeat(context: WorkerContext, lease: Lease) -> bool:
    log_fields = {"submission_id": lease.submission_id, "attempt": lease.attempt}
    try:
        held = extend_lease(context.engine, lease, context.settings.lease_seconds)
    except SQLAlchemyError:
        # If Postgres stays away the lease expires, and fencing voids this result later.
        logger.warning("heartbeat failed", exc_info=True, extra=log_fields)
        return True
    if not held:
        logger.warning("lease lost; stopping the judge", extra=log_fields)
    return held


def _give_back(context: WorkerContext, lease: Lease) -> None:
    log_fields = {"submission_id": lease.submission_id, "attempt": lease.attempt}
    try:
        released = release_lease(context.engine, lease)
    except SQLAlchemyError:
        logger.warning("could not give the lease back", exc_info=True, extra=log_fields)
        released = False
    if released:
        logger.info("shutting down mid-judge; submission put back in the queue", extra=log_fields)
