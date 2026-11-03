import io
import logging
import tarfile
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from docker import DockerClient
from docker.errors import APIError, NotFound
from docker.models.containers import Container
from docker.types import Mount, Ulimit

logger = logging.getLogger(__name__)

SUBMISSION_LABEL = "verdict.submission"
WORKER_LABEL = "verdict.worker"
RUNNER_UID = 1000
BOX = "/box"
MEMORY_HEADROOM_MB = 64
SANDBOX_TMP = "/tmp"  # noqa: S108  (a tmpfs inside the container, not this machine's /tmp)
TMP_MOUNT_OPTIONS = "size=16m"
LIST_OTHER_PIDS = (
    'for entry in /proc/[0-9]*; do pid="${entry#/proc/}"; '
    '[ "$pid" = 1 ] || [ "$pid" = $$ ] || echo "$pid"; done'
)


class SandboxError(Exception):
    """The sandbox container failed or ran past its deadline."""


@dataclass(frozen=True)
class SandboxSpec:
    image: str
    memory_limit_mb: int
    compile_memory_mb: int
    submission: str
    worker_id: str


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    output: bytes


class Sandbox:
    """One running sandbox container; commands run as `runner` in /box."""

    def __init__(self, container: Container, spec: SandboxSpec, idle_pid: int) -> None:
        self._container = container
        self._spec = spec
        self._idle_pid = idle_pid
        self.expired = False

    def put_files(self, files: Mapping[str, bytes], folders: Iterable[str] = ()) -> None:
        """Copy files and empty folders into /box; paths are relative to /box."""
        if not self._container.put_archive(BOX, _tar_owned_by_runner(files, folders)):
            raise SandboxError("copying files into the sandbox failed")

    def read_file(self, path: str) -> bytes:
        try:
            stream, _ = self._container.get_archive(path)
            packed = b"".join(stream)
        except (APIError, NotFound) as error:
            raise self._failure(f"reading {path} failed") from error
        with tarfile.open(fileobj=io.BytesIO(packed)) as archive:
            member = archive.next()
            extracted = archive.extractfile(member) if member else None
            if extracted is None:
                raise SandboxError(f"{path} is not a regular file")
            return extracted.read()

    def run(self, script: str) -> ExecResult:
        try:
            exit_code, output = self._container.exec_run(
                ["sh", "-c", script], user="runner", workdir=BOX
            )
        except APIError as error:
            raise self._failure("running a command failed") from error
        # A command cut short by the watchdog still returns, with status 137 like any SIGKILL.
        if self.expired or exit_code is None or not isinstance(output, bytes):
            raise self._failure("the command did not report an exit code")
        return ExecResult(exit_code, output)

    def run_and_kill_leftovers(self, script: str) -> ExecResult:
        """Run a script, then kill every process it left behind; the status is the script's."""
        return self.run(f"{script}\nstatus=$?\n{_kill_leftovers(self._idle_pid)}")

    def limit_memory_for_tests(self) -> None:
        """Lower the container's memory from the compile budget to the problem's limit."""
        limit = f"{self._spec.memory_limit_mb + MEMORY_HEADROOM_MB}m"
        self._container.update(mem_limit=limit, memswap_limit=limit)

    def expire(self) -> None:
        self.expired = True
        try:
            self._container.kill()
        except APIError:
            logger.warning("could not kill expired sandbox", exc_info=True)

    def _failure(self, message: str) -> SandboxError:
        if self.expired:
            return SandboxError("the sandbox ran past its deadline and was killed")
        return SandboxError(message)


@contextmanager
def open_sandbox(
    client: DockerClient, spec: SandboxSpec, deadline_seconds: float
) -> Iterator[Sandbox]:
    """Start a sandbox, kill it if it outlives `deadline_seconds`, and always remove it."""
    container = _start_container(client, spec)
    try:
        sandbox = Sandbox(container, spec, _idle_pid(container))
        # exec_run has no timeout, so this timer is what guarantees the worker never hangs.
        watchdog = threading.Timer(deadline_seconds, sandbox.expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            yield sandbox
        finally:
            watchdog.cancel()
    finally:
        container.remove(force=True, v=True)


def kill_sandbox(client: DockerClient, submission: str, worker_id: str) -> None:
    """Kill this worker's sandbox for `submission`, so a judge waiting on it returns at once."""
    sandboxes = client.containers.list(
        filters={"label": [f"{SUBMISSION_LABEL}={submission}", f"{WORKER_LABEL}={worker_id}"]}
    )
    for container in sandboxes:
        try:
            container.kill()
        except APIError:
            # It may have finished and been removed between the listing and the kill.
            logger.info("sandbox already gone", extra={"container": container.short_id})


def remove_leftover_sandboxes(client: DockerClient, worker_id: str) -> int:
    """Remove this worker's sandbox containers left behind by a crash; return how many."""
    leftovers = client.containers.list(
        all=True, filters={"label": [SUBMISSION_LABEL, f"{WORKER_LABEL}={worker_id}"]}
    )
    for container in leftovers:
        logger.warning(
            "removing leftover sandbox",
            extra={
                "container": container.short_id,
                "submission": container.labels.get(SUBMISSION_LABEL),
            },
        )
        container.remove(force=True, v=True)
    return len(leftovers)


def _start_container(client: DockerClient, spec: SandboxSpec) -> Container:
    # The container starts with room to compile; limit_memory_for_tests lowers it afterwards.
    memory = f"{max(spec.compile_memory_mb, spec.memory_limit_mb + MEMORY_HEADROOM_MB)}m"
    return client.containers.run(
        spec.image,
        ["sleep", "infinity"],
        detach=True,
        network_disabled=True,
        user="runner",
        working_dir=BOX,
        mem_limit=memory,
        memswap_limit=memory,
        nano_cpus=1_000_000_000,
        pids_limit=64,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        # Only /box and /tmp are writable; put_archive can't copy into a tmpfs, so /box is a volume.
        read_only=True,
        mounts=[Mount(target=BOX, source=None, type="volume")],
        tmpfs={SANDBOX_TMP: TMP_MOUNT_OPTIONS},
        # No /dev/shm, and no room to create message queues in /dev/mqueue, which stays mounted.
        ipc_mode="none",
        ulimits=[Ulimit(name="msgqueue", soft=0, hard=0)],
        # An init process reaps orphans, which would otherwise keep holding the pids limit.
        init=True,
        labels={SUBMISSION_LABEL: spec.submission, WORKER_LABEL: spec.worker_id},
    )


def _idle_pid(container: Container) -> int:
    # Right after start, the only processes are init (pid 1), the idle command, and this shell.
    _, output = container.exec_run(["sh", "-c", LIST_OTHER_PIDS], user="runner")
    pids = output.split() if isinstance(output, bytes) else []
    if len(pids) != 1:
        raise SandboxError(f"expected one idle process, found {pids!r}")
    return int(pids[0])


def _kill_leftovers(idle_pid: int) -> str:
    # Shell built-ins only, so it works even when leftover processes have used up the pids limit.
    # It repeats until nothing is left, because a process can fork while the loop is killing.
    return (
        "while :; do\n"
        "  found=\n"
        "  for entry in /proc/[0-9]*; do\n"
        '    pid="${entry#/proc/}"\n'
        f'    case "$pid" in 1|{idle_pid}|$$) ;; '
        '*) kill -KILL "$pid" 2>/dev/null && found=1 ;; esac\n'
        "  done\n"
        '  [ -z "$found" ] && exit "$status"\n'
        "done"
    )


def _tar_owned_by_runner(files: Mapping[str, bytes], folders: Iterable[str]) -> bytes:
    # put_archive keeps the owner recorded in the tar, so entries are marked as the runner's.
    buffer = io.BytesIO()
    all_folders = sorted({*folders, *(parent for path in files for parent in _parents(path))})
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for folder in all_folders:
            archive.addfile(_entry(folder, tarfile.DIRTYPE, 0o755, 0))
        for path, data in files.items():
            archive.addfile(_entry(path, tarfile.REGTYPE, 0o644, len(data)), io.BytesIO(data))
    return buffer.getvalue()


def _parents(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    return ["/".join(parts[: index + 1]) for index in range(len(parts))]


def _entry(name: str, kind: bytes, mode: int, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    info.size = size
    info.uid = info.gid = RUNNER_UID
    return info
