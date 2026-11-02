from pathlib import Path

import pytest
from docker import DockerClient

from tests.sandbox.conftest import PROGRAMS_DIR, SUM_TESTS, sandbox_containers
from verdict.cli import language_for_file
from verdict.core.verdicts import Verdict
from verdict.judge.runner import JudgeJob, judge

# Each program's file name starts with the verdict it must get, for example tle_sleep.py.
EXPECTED_VERDICTS = {
    "ac": Verdict.ACCEPTED,
    "wa": Verdict.WRONG_ANSWER,
    "tle": Verdict.TIME_LIMIT_EXCEEDED,
    "mle": Verdict.MEMORY_LIMIT_EXCEEDED,
    "re": Verdict.RUNTIME_ERROR,
    "ce": Verdict.COMPILATION_ERROR,
}
PROGRAMS = sorted(
    path for path in PROGRAMS_DIR.iterdir() if path.name.split("_")[0] in EXPECTED_VERDICTS
)


def judge_program(client: DockerClient, program: Path, tests_dir: Path) -> object:
    job = JudgeJob(
        language=language_for_file(program),
        source=program.read_text(),
        time_limit_ms=1000,
        memory_limit_mb=64,
        tests_dir=tests_dir,
        test_count=len(SUM_TESTS),
        submission=f"fixture:{program.name}",
    )
    return judge(client, job, worker_id="sandbox-tests")


def test_every_verdict_has_a_program_in_both_languages_where_it_makes_sense() -> None:
    names = {path.name for path in PROGRAMS}
    for verdict in ("ac", "wa", "tle", "mle", "re"):
        assert any(name.startswith(verdict) and name.endswith(".cpp") for name in names)
        assert any(name.startswith(verdict) and name.endswith(".py") for name in names)
    assert any(name.startswith("ce") for name in names)


@pytest.mark.parametrize("program", PROGRAMS, ids=lambda path: path.name)
def test_program_gets_the_verdict_its_name_promises(
    docker_client: DockerClient, sum_tests: Path, program: Path
) -> None:
    expected = EXPECTED_VERDICTS[program.name.split("_")[0]]
    result = judge_program(docker_client, program, sum_tests)
    assert result.verdict is expected, result
    if expected is Verdict.ACCEPTED:
        assert result.failed_test is None
        assert result.memory_kb
    elif expected is Verdict.COMPILATION_ERROR:
        assert result.compile_output
        assert "error" in result.compile_output
    else:
        assert result.failed_test == 1
    assert sandbox_containers(docker_client) == [], "the sandbox container must be removed"
