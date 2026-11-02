import time

import pytest
from docker import DockerClient

from tests.sandbox.conftest import sandbox_containers
from verdict.core.verdicts import Language
from verdict.judge.runner import RUNNER_IMAGES
from verdict.judge.sandbox import (
    SUBMISSION_LABEL,
    WORKER_LABEL,
    SandboxError,
    SandboxSpec,
    open_sandbox,
    remove_leftover_sandboxes,
)

SPEC = SandboxSpec(
    image=RUNNER_IMAGES[Language.PYTHON],
    memory_limit_mb=64,
    compile_memory_mb=512,
    submission="settings-check",
    worker_id="sandbox-tests",
)
MB = 1024 * 1024


def test_container_starts_with_the_documented_settings(docker_client: DockerClient) -> None:
    with open_sandbox(docker_client, SPEC, deadline_seconds=60):
        [container] = docker_client.containers.list(filters={"label": SUBMISSION_LABEL})
        host = container.attrs["HostConfig"]
        config = container.attrs["Config"]
        assert config["NetworkDisabled"] is True
        assert config["User"] == "runner"
        assert config["WorkingDir"] == "/box"
        assert host["NanoCpus"] == 1_000_000_000
        assert host["PidsLimit"] == 64
        assert host["CapDrop"] == ["ALL"]
        assert host["SecurityOpt"] == ["no-new-privileges"]
        assert host["Tmpfs"] == {"/tmp": "size=16m"}
        assert host["Memory"] == host["MemorySwap"] == 512 * MB
        assert container.labels[WORKER_LABEL] == "sandbox-tests"


def test_memory_is_lowered_to_the_limit_plus_headroom_before_tests(
    docker_client: DockerClient,
) -> None:
    with open_sandbox(docker_client, SPEC, deadline_seconds=60) as sandbox:
        sandbox.limit_memory_for_tests()
        assert sandbox.run("cat /sys/fs/cgroup/memory.max").output.strip() == str(128 * MB).encode()
        assert sandbox.run("cat /sys/fs/cgroup/memory.swap.max").output.strip() == b"0"


def test_files_put_in_belong_to_the_runner(docker_client: DockerClient) -> None:
    with open_sandbox(docker_client, SPEC, deadline_seconds=60) as sandbox:
        sandbox.put_files({"tests/1.in": b"1 2\n"}, folders=["out"])
        listing = sandbox.run("stat -c '%u %n' tests tests/1.in out").output.decode().split("\n")
        assert listing[:3] == ["1000 tests", "1000 tests/1.in", "1000 out"]
        assert sandbox.read_file("/box/tests/1.in") == b"1 2\n"


def test_watchdog_kills_a_sandbox_that_runs_past_its_deadline(
    docker_client: DockerClient,
) -> None:
    started = time.monotonic()
    with (
        pytest.raises(SandboxError, match="past its deadline"),
        open_sandbox(docker_client, SPEC, deadline_seconds=2) as sandbox,
    ):
        sandbox.run("sleep 60")
    assert time.monotonic() - started < 15
    assert sandbox_containers(docker_client) == []


def test_leftovers_from_this_worker_are_removed_and_others_kept(
    docker_client: DockerClient,
) -> None:
    def start(worker: str) -> None:
        docker_client.containers.run(
            SPEC.image,
            ["sleep", "infinity"],
            detach=True,
            labels={SUBMISSION_LABEL: "41", WORKER_LABEL: worker},
        )

    start("crashed-worker")
    start("other-worker")
    try:
        assert remove_leftover_sandboxes(docker_client, "crashed-worker") == 1
        remaining = docker_client.containers.list(all=True, filters={"label": SUBMISSION_LABEL})
        assert [container.labels[WORKER_LABEL] for container in remaining] == ["other-worker"]
    finally:
        remove_leftover_sandboxes(docker_client, "other-worker")
