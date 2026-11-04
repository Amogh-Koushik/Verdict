import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from verdict.cli import main
from verdict.core.config import Settings
from verdict.core.models import Contest, ContestProblem, Problem, ProblemTest, problem_tests_dir
from verdict.samples.problems import SAMPLE_PROBLEMS
from verdict.samples.seed import seed_samples

STARTS_AT = datetime(2026, 12, 1, 4, 30, tzinfo=UTC)


def test_seed_adds_the_five_problems_with_their_tests(db: Session, tmp_path: Path) -> None:
    seed_samples(db, tmp_path, STARTS_AT)

    for sample in SAMPLE_PROBLEMS:
        problem = db.scalars(select(Problem).where(Problem.slug == sample.slug)).one()
        assert (problem.title, problem.is_public) == (sample.title, sample.is_public)
        assert (problem.time_limit_ms, problem.tests_version) == (sample.time_limit_ms, 1)
        stored = db.scalars(
            select(ProblemTest)
            .where(ProblemTest.problem_id == problem.id)
            .order_by(ProblemTest.idx)
        ).all()
        cases = sample.cases()
        assert len(stored) == len(cases)
        folder = problem_tests_dir(tmp_path, problem.id, 1)
        for test, case in zip(stored, cases, strict=True):
            assert (folder / f"{test.idx}.in").read_bytes() == case.given.encode()
            assert test.output_sha256 == hashlib.sha256(case.expected.encode()).hexdigest()


def test_seed_adds_a_contest_with_the_problems_in_order(db: Session, tmp_path: Path) -> None:
    seed_samples(db, tmp_path, STARTS_AT)

    contest = db.scalars(select(Contest).where(Contest.slug == "sample-contest")).one()
    assert (contest.starts_at, contest.ends_at) == (STARTS_AT, STARTS_AT + timedelta(hours=2))
    labels = db.execute(
        select(ContestProblem.label, Problem.slug)
        .join(Problem, Problem.id == ContestProblem.problem_id)
        .where(ContestProblem.contest_id == contest.id)
        .order_by(ContestProblem.label)
    ).all()
    assert [tuple(row) for row in labels] == [
        ("A", "sum-of-two-numbers"),
        ("B", "count-distinct-values"),
        ("C", "shortest-path-in-a-grid"),
        ("D", "longest-increasing-subsequence"),
        ("E", "number-of-connected-components"),
    ]


def test_seeding_again_changes_nothing(db: Session, tmp_path: Path) -> None:
    seed_samples(db, tmp_path, STARTS_AT)

    report = seed_samples(db, tmp_path, STARTS_AT + timedelta(days=1))

    assert all("already exists" in line for line in report)
    assert db.scalar(select(func.count()).select_from(Problem)) == 5
    assert db.scalar(select(func.count()).select_from(Contest)) == 1
    assert set(db.scalars(select(Problem.tests_version))) == {1}


def test_a_problem_that_already_exists_is_kept_and_still_joins_the_contest(
    db: Session, tmp_path: Path
) -> None:
    mine = Problem(slug="sum-of-two-numbers", title="My own version", tests_version=0)
    db.add(mine)
    db.commit()

    report = seed_samples(db, tmp_path, STARTS_AT)

    assert report[0] == "Problem sum-of-two-numbers already exists; left as it is."
    db.refresh(mine)
    assert (mine.title, mine.tests_version) == ("My own version", 0)
    entry = db.scalars(select(ContestProblem).where(ContestProblem.label == "A")).one()
    assert entry.problem_id == mine.id


def test_the_seed_command_starts_the_contest_ten_minutes_from_now(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    engine: Engine,
    db: Session,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("PROBLEMS_DIR", str(tmp_path))

    main(["seed"])

    output = capsys.readouterr().out
    assert "Created problem shortest-path-in-a-grid with 8 tests." in output
    assert "Created contest sample-contest, from " in output
    assert "python -m verdict.cli try-run <file> <problem-slug>" in output
    starts_in = db.scalar(select(Contest.starts_at - func.now()))
    assert starts_in is not None
    assert timedelta(minutes=9) < starts_in <= timedelta(minutes=10)


def test_the_seeded_contests_hidden_problems_open_at_the_start(
    client: TestClient, db: Session, tmp_path: Path
) -> None:
    seed_samples(db, tmp_path, datetime.now(UTC) + timedelta(minutes=10))
    listing = client.get("/problems").text
    assert "Sum of two numbers" in listing
    assert "Shortest path in a grid" not in listing
    contest = db.scalars(select(Contest)).one()
    contest.starts_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert "Shortest path in a grid" in client.get("/problems").text
