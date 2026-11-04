from functools import cache
from pathlib import Path

import pytest
from docker import DockerClient

from verdict.cli import describe
from verdict.core.config import load_settings
from verdict.core.verdicts import Language, Verdict
from verdict.judge.runner import JudgeJob, judge
from verdict.samples.cases import Case
from verdict.samples.problems import SAMPLE_PROBLEMS, SampleProblem

LANGUAGES = {".cpp": Language.CPP, ".py": Language.PYTHON}


@cache
def cases_of(sample: SampleProblem) -> list[Case]:
    return sample.cases()


@pytest.mark.parametrize("suffix", LANGUAGES)
@pytest.mark.parametrize("sample", SAMPLE_PROBLEMS, ids=lambda sample: sample.slug)
def test_every_reference_solution_is_accepted_well_inside_the_time_limit(
    docker_client: DockerClient, tmp_path: Path, sample: SampleProblem, suffix: str
) -> None:
    cases = cases_of(sample)
    for idx, case in enumerate(cases, start=1):
        (tmp_path / f"{idx}.in").write_text(case.given, newline="\n")
        (tmp_path / f"{idx}.out").write_text(case.expected, newline="\n")
    language = LANGUAGES[suffix]
    time_limit_ms = sample.time_limit_ms * (
        load_settings().python_time_multiplier if language is Language.PYTHON else 1
    )
    job = JudgeJob(
        language=language,
        source=sample.solution(suffix).read_text(encoding="utf-8"),
        time_limit_ms=round(time_limit_ms),
        memory_limit_mb=256,
        tests_dir=tmp_path,
        test_count=len(cases),
        submission=f"sample-{sample.slug}{suffix}",
    )

    result = judge(docker_client, job, worker_id="sample-checks")

    assert result.verdict is Verdict.ACCEPTED, describe(result)
    # A reference solution near the limit would make the limit itself unfair.
    assert result.time_ms is not None
    assert result.time_ms <= time_limit_ms / 2, f"{result.time_ms} ms of {time_limit_ms} ms"
