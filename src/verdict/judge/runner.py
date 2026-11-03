import logging
from dataclasses import dataclass
from pathlib import Path

from docker import DockerClient

from verdict.core.models import MAX_COMPILE_OUTPUT_BYTES
from verdict.core.verdicts import Language, Verdict
from verdict.judge.checker import outputs_match
from verdict.judge.classify import Limits, RunStats, classify, wall_seconds_for
from verdict.judge.sandbox import Sandbox, SandboxError, SandboxSpec, open_sandbox

logger = logging.getLogger(__name__)

RUNNER_IMAGES = {Language.CPP: "verdict-runner-cpp:1", Language.PYTHON: "verdict-runner-python:1"}
SOURCE_FILES = {Language.CPP: "main.cpp", Language.PYTHON: "main.py"}
RUN_COMMANDS = {Language.CPP: "./prog", Language.PYTHON: "python3 -B main.py"}
COMPILE_SECONDS = 10
COMPILE_COMMAND = f"timeout {COMPILE_SECONDS} g++ -std=c++17 -O2 -pipe -o /box/prog /box/main.cpp"
COMPILE_TIMED_OUT = 124
# g++ needs about 210 MB for a typical solution, more than small problem limits allow.
COMPILE_MEMORY_MB = 512
OUTPUT_LIMIT_BYTES = 32 * 1024 * 1024
# Container start-up and the copies before and after each test, on top of the time limits.
FIXED_OVERHEAD_SECONDS = 15
PER_TEST_OVERHEAD_SECONDS = 2


@dataclass(frozen=True)
class JudgeJob:
    language: Language
    source: str
    time_limit_ms: int
    memory_limit_mb: int
    tests_dir: Path
    test_count: int
    submission: str


@dataclass(frozen=True)
class JudgeResult:
    verdict: Verdict
    failed_test: int | None = None
    time_ms: int | None = None
    memory_kb: int | None = None
    compile_output: str | None = None


def judge(client: DockerClient, job: JudgeJob, worker_id: str) -> JudgeResult:
    """Judge one submission in a fresh sandbox; any unexpected failure becomes a system error."""
    try:
        return _judge(client, job, worker_id)
    except Exception:
        logger.exception("judging failed", extra={"submission": job.submission})
        return JudgeResult(Verdict.SYSTEM_ERROR)


def parse_time_report(report: str, exit_status: int, *, oom_killed: bool) -> RunStats:
    """Read GNU time's last line, "elapsed user system max_rss_kb"; earlier lines are notices."""
    try:
        elapsed, user, system, max_rss_kb = report.strip().splitlines()[-1].split()
        return RunStats(
            elapsed_seconds=float(elapsed),
            cpu_seconds=float(user) + float(system),
            max_rss_kb=int(max_rss_kb),
            exit_status=exit_status,
            oom_killed=oom_killed,
        )
    except (IndexError, ValueError):
        raise SandboxError(f"unreadable time report: {report!r}") from None


def _judge(client: DockerClient, job: JudgeJob, worker_id: str) -> JudgeResult:
    limits = Limits(
        time_limit_ms=job.time_limit_ms,
        memory_limit_kb=job.memory_limit_mb * 1024,
        wall_seconds=wall_seconds_for(job.time_limit_ms),
    )
    spec = SandboxSpec(
        image=RUNNER_IMAGES[job.language],
        memory_limit_mb=job.memory_limit_mb,
        compile_memory_mb=COMPILE_MEMORY_MB,
        submission=job.submission,
        worker_id=worker_id,
    )
    deadline = (
        COMPILE_SECONDS
        + job.test_count * (limits.wall_seconds + PER_TEST_OVERHEAD_SECONDS)
        + FIXED_OVERHEAD_SECONDS
    )
    with open_sandbox(client, spec, deadline) as sandbox:
        sandbox.put_files(_sandbox_files(job), folders=["out"])
        if job.language is Language.CPP:
            compiled = sandbox.run(COMPILE_COMMAND)
            if compiled.exit_code != 0:
                return JudgeResult(
                    Verdict.COMPILATION_ERROR,
                    compile_output=_compile_output(compiled.exit_code, compiled.output),
                )
        sandbox.limit_memory_for_tests()
        return _run_tests(sandbox, job, limits)


def _sandbox_files(job: JudgeJob) -> dict[str, bytes]:
    # Only the inputs go in; expected outputs stay with the worker so a program can't read them.
    files = {SOURCE_FILES[job.language]: job.source.encode()}
    for idx in range(1, job.test_count + 1):
        files[f"tests/{idx}.in"] = (job.tests_dir / f"{idx}.in").read_bytes()
    return files


def _run_tests(sandbox: Sandbox, job: JudgeJob, limits: Limits) -> JudgeResult:
    oom_kills = _oom_kill_count(sandbox)
    time_ms = memory_kb = 0
    for idx in range(1, job.test_count + 1):
        script = _test_script(job.language, idx, limits)
        exit_status = sandbox.run_and_kill_leftovers(script).exit_code
        report = sandbox.read_file(f"/box/out/{idx}.time").decode()
        oom_kills_after = _oom_kill_count(sandbox)
        stats = parse_time_report(report, exit_status, oom_killed=oom_kills_after > oom_kills)
        oom_kills = oom_kills_after
        time_ms = max(time_ms, round(stats.cpu_seconds * 1000))
        memory_kb = max(memory_kb, stats.max_rss_kb)
        verdict = classify(stats, limits)
        if verdict is None:
            verdict = _check_output(sandbox, job, idx)
        if verdict is not None:
            return JudgeResult(verdict, idx, time_ms, memory_kb)
    return JudgeResult(Verdict.ACCEPTED, None, time_ms, memory_kb)


def _test_script(language: Language, idx: int, limits: Limits) -> str:
    # ulimit -f counts 512-byte blocks in this shell; it stops a program that prints forever.
    return (
        f"ulimit -f {OUTPUT_LIMIT_BYTES // 512} && "
        f"/usr/bin/time -f '%e %U %S %M' -o /box/out/{idx}.time "
        f"timeout -s KILL {limits.wall_seconds:g} {RUN_COMMANDS[language]} "
        f"< /box/tests/{idx}.in > /box/out/{idx}.out 2> /dev/null"
    )


def _check_output(sandbox: Sandbox, job: JudgeJob, idx: int) -> Verdict | None:
    expected = (job.tests_dir / f"{idx}.out").read_bytes()
    actual = sandbox.read_file(f"/box/out/{idx}.out")
    return None if outputs_match(expected, actual) else Verdict.WRONG_ANSWER


def _oom_kill_count(sandbox: Sandbox) -> int:
    events = sandbox.run("cat /sys/fs/cgroup/memory.events").output.decode()
    for line in events.splitlines():
        name, _, value = line.partition(" ")
        if name == "oom_kill":
            return int(value)
    raise SandboxError("memory.events has no oom_kill counter")


def _compile_output(exit_code: int, output: bytes) -> str:
    if exit_code == COMPILE_TIMED_OUT:
        return f"Compilation took longer than {COMPILE_SECONDS} seconds."
    # Cutting bytes can split a UTF-8 character; ignoring the broken tail keeps the size limit.
    return output[:MAX_COMPILE_OUTPUT_BYTES].decode("utf-8", errors="ignore")
