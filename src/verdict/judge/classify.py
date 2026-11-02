from dataclasses import dataclass

from verdict.core.verdicts import Verdict

# timeout -s KILL, and the kernel's out-of-memory killer, both end the program with SIGKILL (9),
# which the shell reports as 128 + 9.
KILLED_STATUS = 137
# GNU time reports elapsed time in hundredths of a second.
WALL_TOLERANCE_SECONDS = 0.05


@dataclass(frozen=True)
class RunStats:
    """One test run, as reported by GNU time and the container's memory cgroup."""

    elapsed_seconds: float
    cpu_seconds: float
    max_rss_kb: int
    exit_status: int
    oom_killed: bool


@dataclass(frozen=True)
class Limits:
    time_limit_ms: int
    memory_limit_kb: int
    wall_seconds: float


def wall_seconds_for(time_limit_ms: int) -> float:
    """Twice the time limit plus a second, so programs that sleep or wait still get stopped."""
    return 2 * time_limit_ms / 1000 + 1


def classify(run: RunStats, limits: Limits) -> Verdict | None:
    """The verdict a run earns on its own, or None if it ran fine and its output decides."""
    killed_at_wall = (
        run.exit_status == KILLED_STATUS
        and run.elapsed_seconds >= limits.wall_seconds - WALL_TOLERANCE_SECONDS
    )
    if run.cpu_seconds * 1000 >= limits.time_limit_ms or killed_at_wall:
        return Verdict.TIME_LIMIT_EXCEEDED
    if run.max_rss_kb >= limits.memory_limit_kb or run.oom_killed:
        return Verdict.MEMORY_LIMIT_EXCEEDED
    if run.exit_status != 0:
        return Verdict.RUNTIME_ERROR
    return None
