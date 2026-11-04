from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from verdict.core.models import Contest, ContestProblem, Problem, Submission, User
from verdict.core.verdicts import Language, SubmissionStatus, Verdict


def add_contest(
    db: Session,
    *,
    starts_in: timedelta,
    lasts: timedelta = timedelta(hours=2),
    slug: str = "weekly",
) -> Contest:
    """A contest starting `starts_in` from now; a negative value means it has already started."""
    starts_at = datetime.now(UTC) + starts_in
    contest = Contest(slug=slug, title=slug.title(), starts_at=starts_at, ends_at=starts_at + lasts)
    db.add(contest)
    db.commit()
    return contest


def add_contest_problem(db: Session, contest: Contest, label: str, slug: str) -> Problem:
    """A hidden problem with one version of tests, in `contest` under `label`."""
    problem = Problem(slug=slug, title=slug.replace("-", " ").title(), tests_version=1)
    db.add(problem)
    db.flush()
    db.add(ContestProblem(contest_id=contest.id, label=label, problem_id=problem.id))
    db.commit()
    return problem


def add_contest_submission(
    db: Session,
    contest: Contest,
    user: User,
    problem: Problem,
    *,
    minute: float,
    verdict: Verdict | None,
    practice: bool = False,
    source: str = "print(1)",
) -> Submission:
    """A submission made `minute` minutes into `contest`; still queued when `verdict` is None."""
    submission = Submission(
        user_id=user.id,
        problem_id=problem.id,
        contest_id=None if practice else contest.id,
        language=Language.PYTHON,
        source=source,
        status=SubmissionStatus.QUEUED if verdict is None else SubmissionStatus.DONE,
        verdict=verdict,
        created_at=contest.starts_at + timedelta(minutes=minute),
    )
    db.add(submission)
    db.commit()
    return submission
