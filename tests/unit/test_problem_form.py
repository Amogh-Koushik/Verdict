from dataclasses import replace

import pytest

from verdict.core.models import Problem
from verdict.core.verdicts import Language
from verdict.web.problem_form import ProblemForm, ProblemFormError

VALID = ProblemForm(
    slug="sum-of-two",
    title="Sum of two numbers",
    statement_md="Print a + b.",
    time_limit_ms="1000",
    memory_limit_mb="256",
    is_public=True,
)


def test_valid_form_is_copied_onto_the_problem() -> None:
    problem = Problem()
    VALID.apply_to(problem)
    assert problem.slug == "sum-of-two"
    assert problem.title == "Sum of two numbers"
    assert problem.time_limit_ms == 1000
    assert problem.memory_limit_mb == 256
    assert problem.is_public is True


@pytest.mark.parametrize(
    "changes",
    [
        {"slug": "a"},
        {"slug": "x" * 64},
        {"time_limit_ms": "100"},
        {"time_limit_ms": "10000"},
        {"memory_limit_mb": "16"},
        {"memory_limit_mb": "1024"},
        {"title": "t" * 200},
    ],
)
def test_values_at_the_limits_are_accepted(changes: dict[str, str]) -> None:
    replace(VALID, **changes).apply_to(Problem())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"slug": ""}, "slug"),
        ({"slug": "Sum"}, "slug"),
        ({"slug": "sum_two"}, "slug"),
        ({"slug": "sum--two"}, "slug"),
        ({"slug": "-sum"}, "slug"),
        ({"slug": "x" * 65}, "slug"),
        ({"title": ""}, "title"),
        ({"title": "t" * 201}, "title"),
        ({"statement_md": "s" * 100_001}, "statement"),
        ({"time_limit_ms": "fast"}, "Time limit must be a whole number"),
        ({"time_limit_ms": "1.5"}, "Time limit must be a whole number"),
        ({"time_limit_ms": "99"}, "Time limit must be between 100 and 10000 ms"),
        ({"time_limit_ms": "10001"}, "Time limit must be between"),
        ({"memory_limit_mb": "15"}, "Memory limit must be between 16 and 1024 MB"),
        ({"memory_limit_mb": ""}, "Memory limit must be a whole number"),
    ],
)
def test_invalid_value_is_rejected(changes: dict[str, str], message: str) -> None:
    with pytest.raises(ProblemFormError, match=message):
        replace(VALID, **changes).apply_to(Problem())


def test_rejected_form_leaves_the_problem_unchanged() -> None:
    problem = Problem(slug="old", title="Old title", time_limit_ms=2000)
    with pytest.raises(ProblemFormError):
        replace(VALID, memory_limit_mb="0").apply_to(problem)
    assert (problem.slug, problem.title, problem.time_limit_ms) == ("old", "Old title", 2000)


def test_python_gets_the_multiplied_time_limit() -> None:
    problem = Problem(time_limit_ms=1500)
    assert problem.time_limit_ms_for(Language.CPP, 2.0) == 1500
    assert problem.time_limit_ms_for(Language.PYTHON, 2.0) == 3000
    assert problem.time_limit_ms_for(Language.PYTHON, 1.5) == 2250
