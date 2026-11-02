from collections.abc import Iterator
from pathlib import Path

import docker
import pytest
from docker import DockerClient

from verdict.judge.sandbox import SUBMISSION_LABEL

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
