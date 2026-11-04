from dataclasses import replace

import pytest

from verdict.core.verdicts import Verdict
from verdict.judge.classify import Limits, RunStats, classify, wall_seconds_for

LIMITS = Limits(time_limit_ms=1000, memory_limit_kb=256 * 1024, wall_seconds=3.0)
CLEAN = RunStats(
    elapsed_seconds=0.12, cpu_seconds=0.10, max_rss_kb=4_000, exit_status=0, oom_killed=False
)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param({}, None, id="clean run"),
        pytest.param({"cpu_seconds": 0.999}, None, id="cpu just under limit"),
        pytest.param({"cpu_seconds": 1.0}, Verdict.TIME_LIMIT_EXCEEDED, id="cpu at limit"),
        pytest.param(
            {"cpu_seconds": 1.4, "exit_status": 0},
            Verdict.TIME_LIMIT_EXCEEDED,
            id="finished but used too much cpu",
        ),
        pytest.param(
            {"cpu_seconds": 3.0, "elapsed_seconds": 3.0, "exit_status": 137},
            Verdict.TIME_LIMIT_EXCEEDED,
            id="busy loop killed at wall",
        ),
        pytest.param(
            {"cpu_seconds": 0.0, "elapsed_seconds": 3.0, "exit_status": 137},
            Verdict.TIME_LIMIT_EXCEEDED,
            id="sleeper killed at wall",
        ),
        pytest.param(
            {"cpu_seconds": 0.0, "elapsed_seconds": 2.96, "exit_status": 137},
            Verdict.TIME_LIMIT_EXCEEDED,
            id="wall kill within rounding",
        ),
        pytest.param({"max_rss_kb": 256 * 1024 - 1}, None, id="memory just under limit"),
        pytest.param(
            {"max_rss_kb": 256 * 1024}, Verdict.MEMORY_LIMIT_EXCEEDED, id="memory at limit"
        ),
        pytest.param(
            {"max_rss_kb": 300_000, "exit_status": 137, "oom_killed": True},
            Verdict.MEMORY_LIMIT_EXCEEDED,
            id="killed by memory cap",
        ),
        pytest.param(
            {"max_rss_kb": 1_000, "exit_status": 137, "oom_killed": True},
            Verdict.MEMORY_LIMIT_EXCEEDED,
            id="oom kill even with low reported peak",
        ),
        pytest.param(
            {"max_rss_kb": 300_000, "exit_status": 1},
            Verdict.MEMORY_LIMIT_EXCEEDED,
            id="python MemoryError over the limit",
        ),
        pytest.param({"exit_status": 1}, Verdict.RUNTIME_ERROR, id="uncaught exception"),
        pytest.param({"exit_status": 3}, Verdict.RUNTIME_ERROR, id="non-zero exit"),
        pytest.param({"exit_status": 139}, Verdict.RUNTIME_ERROR, id="segfault"),
        pytest.param({"exit_status": 153}, Verdict.RUNTIME_ERROR, id="output file too large"),
        pytest.param(
            {"exit_status": 137, "elapsed_seconds": 0.5},
            Verdict.RUNTIME_ERROR,
            id="killed early, not by wall or memory",
        ),
        pytest.param(
            {"cpu_seconds": 2.0, "max_rss_kb": 300_000, "exit_status": 137, "oom_killed": True},
            Verdict.TIME_LIMIT_EXCEEDED,
            id="time is checked before memory",
        ),
        pytest.param(
            {"max_rss_kb": 300_000, "exit_status": 139},
            Verdict.MEMORY_LIMIT_EXCEEDED,
            id="memory is checked before exit status",
        ),
    ],
)
def test_a_run_gets_the_verdict_its_measurements_call_for(
    changes: dict[str, object], expected: Verdict | None
) -> None:
    assert classify(replace(CLEAN, **changes), LIMITS) == expected


@pytest.mark.parametrize(
    ("time_limit_ms", "wall_seconds"), [(1000, 3.0), (2000, 5.0), (100, 1.2), (1500, 4.0)]
)
def test_wall_limit_is_twice_the_time_limit_plus_a_second(
    time_limit_ms: int, wall_seconds: float
) -> None:
    assert wall_seconds_for(time_limit_ms) == pytest.approx(wall_seconds)
