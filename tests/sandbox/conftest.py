import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import docker
import pytest
from docker import DockerClient
from redis import Redis
from sqlalchemy import Engine

from verdict.core.config import Settings
from verdict.judge.listener import QueueListener
from verdict.judge.sandbox import SUBMISSION_LABEL
from verdict.judge.worker import WorkerContext, run_worker

PROGRAMS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "programs"
SUM_TESTS = [("1 2\n", "3\n"), ("-5 5\n", "0\n"), ("1000000000 1000000000\n", "2000000000\n")]


@pytest.fixture(scope="session")
def docker_client() -> Iterator[DockerClient]:
    client = docker.from_env()
    yield client
    client.close()


@pytest.fixture
def sum_tests(tmp_path: Path) -> Path:
    """Tests for "print the sum of two integers", written as 1.in, 1.out, 2.in, ..."""
    for idx, (given, expected) in enumerate(SUM_TESTS, start=1):
        (tmp_path / f"{idx}.in").write_text(given)
        (tmp_path / f"{idx}.out").write_text(expected)
    return tmp_path


def sandbox_containers(client: DockerClient) -> list[str]:
    containers = client.containers.list(all=True, filters={"label": SUBMISSION_LABEL})
    return [container.short_id for container in containers]


def wait_until(condition: Callable[[], bool], timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


class RunningWorker:
    def __init__(self, context: WorkerContext) -> None:
        self.context = context
        self._listener = QueueListener(context.settings.database_url)
        self._thread = threading.Thread(target=run_worker, args=(context, self._listener.wait))

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.context.stop.set()

    def join(self, timeout_seconds: float) -> bool:
        self._thread.join(timeout_seconds)
        self._listener.close()
        return not self._thread.is_alive()


@pytest.fixture
def start_worker(
    app_settings: Settings, engine: Engine, redis: Redis, docker_client: DockerClient
) -> Iterator[Callable[..., RunningWorker]]:
    workers: list[RunningWorker] = []

    def start(shutdown_grace_seconds: float = 10, **overrides: int) -> RunningWorker:
        context = WorkerContext(
            settings=replace(app_settings, **overrides),
            engine=engine,
            redis=redis,
            docker=docker_client,
            worker_id=f"test-worker-{len(workers)}",
            stop=threading.Event(),
            shutdown_grace_seconds=shutdown_grace_seconds,
        )
        worker = RunningWorker(context)
        workers.append(worker)
        worker.start()
        return worker

    yield start
    for worker in workers:
        worker.stop()
        assert worker.join(30), "worker did not stop"
