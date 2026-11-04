import io
import zipfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from verdict.core.models import Contest, ContestProblem, Problem
from verdict.samples.cases import Case
from verdict.samples.problems import (
    SAMPLE_CONTEST_LENGTH,
    SAMPLE_CONTEST_SLUG,
    SAMPLE_CONTEST_TITLE,
    SAMPLE_PROBLEMS,
    SampleProblem,
)
from verdict.web.templating import format_ist
from verdict.web.test_archive import store_test_archive

CONTEST_LABELS = "ABCDE"


def seed_samples(db: Session, problems_dir: Path, contest_starts_at: datetime) -> list[str]:
    """Add the sample problems with their tests, and the sample contest; existing ones are kept."""
    report: list[str] = []
    problems: list[Problem] = []
    for sample in SAMPLE_PROBLEMS:
        problem = db.scalars(select(Problem).where(Problem.slug == sample.slug)).one_or_none()
        if problem is None:
            problem, test_count = _create_problem(db, sample, problems_dir)
            report.append(f"Created problem {sample.slug} with {test_count} tests.")
        else:
            report.append(f"Problem {sample.slug} already exists; left as it is.")
        problems.append(problem)
    contest = db.scalars(select(Contest).where(Contest.slug == SAMPLE_CONTEST_SLUG)).one_or_none()
    if contest is None:
        contest = _create_contest(db, problems, contest_starts_at)
        report.append(
            f"Created contest {contest.slug}, from {format_ist(contest.starts_at)} "
            f"to {format_ist(contest.ends_at)}."
        )
    else:
        report.append(f"Contest {SAMPLE_CONTEST_SLUG} already exists; left as it is.")
    return report


def _create_problem(db: Session, sample: SampleProblem, problems_dir: Path) -> tuple[Problem, int]:
    problem = Problem(
        slug=sample.slug,
        title=sample.title,
        statement_md=sample.statement_md,
        time_limit_ms=sample.time_limit_ms,
        is_public=sample.is_public,
    )
    db.add(problem)
    # Not committed yet: storing the tests commits both, or rolls both back if the tests fail.
    db.flush()
    cases = sample.cases()
    store_test_archive(db, problem.id, _zip_of(cases), problems_dir)
    return problem, len(cases)


def _create_contest(db: Session, problems: Sequence[Problem], starts_at: datetime) -> Contest:
    contest = Contest(
        slug=SAMPLE_CONTEST_SLUG,
        title=SAMPLE_CONTEST_TITLE,
        starts_at=starts_at,
        ends_at=starts_at + SAMPLE_CONTEST_LENGTH,
    )
    db.add(contest)
    db.flush()
    db.add_all(
        ContestProblem(contest_id=contest.id, label=label, problem_id=problem.id)
        for label, problem in zip(CONTEST_LABELS, problems, strict=True)
    )
    db.commit()
    return contest


def _zip_of(cases: Sequence[Case]) -> io.BytesIO:
    # Stored, not compressed: the archive never leaves memory, so compressing only costs time.
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zipped:
        for idx, case in enumerate(cases, start=1):
            zipped.writestr(f"{idx}.in", case.given)
            zipped.writestr(f"{idx}.out", case.expected)
    archive.seek(0)
    return archive
