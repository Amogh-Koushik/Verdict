from sqlalchemy.orm import Session

from verdict.core.accounts import create_user
from verdict.core.models import Problem, Submission
from verdict.core.verdicts import Language


def add_submissions(db: Session, count: int, source: str = "print(3)") -> list[int]:
    """Queue `count` Python submissions to a problem with one test version; return their ids."""
    user = create_user(db, f"user{count}", "a long enough password")
    problem = Problem(slug=f"problem-{count}", title="Sum", tests_version=1)
    db.add(problem)
    db.flush()
    submissions = [
        Submission(user_id=user.id, problem_id=problem.id, language=Language.PYTHON, source=source)
        for _ in range(count)
    ]
    db.add_all(submissions)
    db.commit()
    return [submission.id for submission in submissions]
