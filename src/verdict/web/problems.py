from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from verdict.core.config import Settings
from verdict.core.models import Contest, ContestProblem, Problem
from verdict.core.verdicts import Language
from verdict.web.deps import DbDep, SettingsDep, ViewerDep
from verdict.web.markdown import render_markdown
from verdict.web.templating import render
from verdict.web.visibility import visible_problems

router = APIRouter()

LANGUAGE_NAMES = {Language.CPP: "C++17 (g++ 14)", Language.PYTHON: "Python 3.12"}


@router.get("/problems")
def problem_list(request: Request, viewer: ViewerDep, db: DbDep) -> HTMLResponse:
    problems = db.scalars(visible_problems(viewer.user).order_by(Problem.id)).all()
    return render(request, viewer, "problems/list.html", {"problems": problems})


@router.get("/problems/{slug}")
def problem_page(
    request: Request, slug: str, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> HTMLResponse:
    problem = db.scalars(visible_problems(viewer.user).where(Problem.slug == slug)).one_or_none()
    if problem is None:
        # 404 rather than 403, so a hidden problem's slug doesn't reveal that it exists.
        raise HTTPException(status_code=404)
    context = practice_page_context(db, problem, settings)
    return render(request, viewer, "problems/view.html", context)


def problem_page_context(problem: Problem, settings: Settings) -> dict[str, Any]:
    """What problems/view.html shows for a problem, inside a contest or not."""
    multiplier = settings.python_time_multiplier
    return {
        "problem": problem,
        "statement": render_markdown(problem.statement_md),
        "python_multiplier": multiplier,
        "python_time_limit_ms": problem.time_limit_ms_for(Language.PYTHON, multiplier),
        "languages": LANGUAGE_NAMES,
    }


def practice_page_context(db: Session, problem: Problem, settings: Settings) -> dict[str, Any]:
    """The problem's own page, where submissions are practice."""
    return {
        **problem_page_context(problem, settings),
        "submit_url": f"/problems/{problem.slug}/submit",
        # Only submissions from a contest's page count for it, so contestants are sent there.
        "running_contests": _running_contests_with(db, problem),
    }


def _running_contests_with(db: Session, problem: Problem) -> Sequence[Contest]:
    return db.scalars(
        select(Contest)
        .join(ContestProblem, ContestProblem.contest_id == Contest.id)
        .where(
            ContestProblem.problem_id == problem.id,
            Contest.starts_at <= func.now(),
            Contest.ends_at > func.now(),
        )
        .order_by(Contest.starts_at)
    ).all()
