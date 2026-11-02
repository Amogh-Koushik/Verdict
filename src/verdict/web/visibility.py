from sqlalchemy import ColumnElement, Select, exists, func, or_, select

from verdict.core.models import Contest, ContestProblem, Problem, User


def problem_is_visible() -> ColumnElement[bool]:
    """True for problems a non-admin may see: public ones, or ones in a contest that has started."""
    in_started_contest = exists().where(
        ContestProblem.problem_id == Problem.id,
        ContestProblem.contest_id == Contest.id,
        Contest.starts_at <= func.now(),
    )
    return or_(Problem.is_public, in_started_contest)


def visible_problems(user: User | None) -> Select[tuple[Problem]]:
    query = select(Problem)
    if user is None or not user.is_admin:
        query = query.where(problem_is_visible())
    return query
