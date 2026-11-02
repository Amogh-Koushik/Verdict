from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from verdict.core.models import Problem
from verdict.core.verdicts import Language
from verdict.web.deps import DbDep, SettingsDep, ViewerDep
from verdict.web.markdown import render_markdown
from verdict.web.templating import render
from verdict.web.visibility import visible_problems

router = APIRouter()


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
    multiplier = settings.python_time_multiplier
    context = {
        "problem": problem,
        "statement": render_markdown(problem.statement_md),
        "python_multiplier": multiplier,
        "python_time_limit_ms": problem.time_limit_ms_for(Language.PYTHON, multiplier),
    }
    return render(request, viewer, "problems/view.html", context)
