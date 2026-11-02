import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.sandbox.conftest import PROGRAMS_DIR
from verdict.cli import main
from verdict.core.config import Settings
from verdict.core.models import Problem, ProblemTest, problem_tests_dir


@pytest.fixture
def sum_problem(db: Session, app_settings: Settings, sum_tests: Path) -> Problem:
    problem = Problem(slug="sum", title="Sum", tests_version=1, memory_limit_mb=64)
    db.add(problem)
    db.commit()
    shutil.copytree(sum_tests, problem_tests_dir(app_settings.problems_dir, problem.id, 1))
    db.add_all(
        ProblemTest(problem_id=problem.id, version=1, idx=idx, input_sha256="", output_sha256="")
        for idx in (1, 2, 3)
    )
    db.commit()
    return problem


@pytest.fixture
def cli_settings(monkeypatch: pytest.MonkeyPatch, app_settings: Settings) -> None:
    monkeypatch.setenv("DATABASE_URL", app_settings.database_url)
    monkeypatch.setenv("PROBLEMS_DIR", str(app_settings.problems_dir))


@pytest.mark.usefixtures("cli_settings", "sum_problem")
@pytest.mark.parametrize(
    ("program", "expected"),
    [("ac_sum.cpp", "Verdict: AC"), ("wa_off_by_one.py", "Verdict: WA\nFailed test: 1")],
)
def test_try_run_judges_a_file_against_the_problems_tests(
    capsys: pytest.CaptureFixture[str], program: str, expected: str
) -> None:
    main(["try-run", str(PROGRAMS_DIR / program), "sum"])
    assert capsys.readouterr().out.startswith(expected)


@pytest.mark.usefixtures("cli_settings")
def test_try_run_refuses_an_unknown_problem() -> None:
    with pytest.raises(SystemExit, match="No problem has the slug 'missing'"):
        main(["try-run", str(PROGRAMS_DIR / "ac_sum.py"), "missing"])


def test_try_run_refuses_a_file_of_unknown_language(tmp_path: Path) -> None:
    source = tmp_path / "solution.rb"
    source.write_text("puts 3")
    with pytest.raises(SystemExit, match=r"name it \.cpp or \.py"):
        main(["try-run", str(source), "sum"])
