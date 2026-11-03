from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from verdict.core.models import Problem, Submission, User
from verdict.web.csrf import CSRF_PROTECTED
from verdict.web.deps import DbDep, RedisDep, SettingsDep, ViewerDep
from verdict.web.problems import practice_page_context
from verdict.web.submitting import (
    SubmissionFormDep,
    SubmissionRefusedError,
    accept_submission,
    refusal_page,
)
from verdict.web.templating import render
from verdict.web.visibility import visible_problems

router = APIRouter()

RECENT_SUBMISSIONS = 50


@router.post("/problems/{slug}/submit", dependencies=CSRF_PROTECTED)
def submit(
    request: Request,
    slug: str,
    viewer: ViewerDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    form: SubmissionFormDep,
) -> Response:
    if viewer.user is None:
        return RedirectResponse("/login", status_code=303)
    problem = _visible_problem(db, viewer.user, slug)
    try:
        # Outside a contest's page every submission is practice.
        submission = accept_submission(
            db, redis, settings, form, user=viewer.user, problem=problem, contest_id=None
        )
    except SubmissionRefusedError as refusal:
        page_context = practice_page_context(db, problem, settings)
        return refusal_page(request, viewer, page_context, form, refusal)
    return RedirectResponse(f"/submissions/{submission.id}", status_code=303)


@router.get("/submissions")
def my_submissions(request: Request, viewer: ViewerDep, db: DbDep) -> Response:
    if viewer.user is None:
        return RedirectResponse("/login", status_code=303)
    rows = db.execute(
        select(Submission, Problem)
        .join(Problem, Problem.id == Submission.problem_id)
        .where(Submission.user_id == viewer.user.id)
        .order_by(Submission.id.desc())
        .limit(RECENT_SUBMISSIONS)
    ).all()
    pending_ids = [submission.id for submission, _ in rows if submission.is_pending]
    context = {"rows": rows, "pending_ids": pending_ids}
    return render(request, viewer, "submissions/list.html", context)


@router.get("/submissions/{submission_id}")
def submission_page(
    request: Request, submission_id: int, viewer: ViewerDep, db: DbDep
) -> HTMLResponse:
    submission, problem = _viewable_submission(db, viewer.user, submission_id)
    context = {"submission": submission, "problem": problem}
    return render(request, viewer, "submissions/view.html", context)


@router.get("/submissions/{submission_id}/row")
def submission_row(
    request: Request, submission_id: int, viewer: ViewerDep, db: DbDep
) -> HTMLResponse:
    """The submission's row in the list, re-fetched by the page when the submission changes."""
    submission, problem = _viewable_submission(db, viewer.user, submission_id)
    context = {"submission": submission, "problem": problem}
    return render(request, viewer, "submissions/_row.html", context)


@router.get("/submissions/{submission_id}/result")
def submission_result(
    request: Request, submission_id: int, viewer: ViewerDep, db: DbDep
) -> HTMLResponse:
    """The result part of the submission page, re-fetched when the submission changes."""
    submission, problem = _viewable_submission(db, viewer.user, submission_id)
    context = {"submission": submission, "problem": problem}
    return render(request, viewer, "submissions/_result.html", context)


def _viewable_submission(
    db: Session, user: User | None, submission_id: int
) -> tuple[Submission, Problem]:
    submission = db.get(Submission, submission_id)
    # Only the author and administrators may see a submission and its source code.
    if submission is None or user is None or not (user.is_admin or submission.user_id == user.id):
        raise HTTPException(status_code=404)
    return submission, db.get_one(Problem, submission.problem_id)


def _visible_problem(db: Session, user: User, slug: str) -> Problem:
    problem = db.scalars(visible_problems(user).where(Problem.slug == slug)).one_or_none()
    if problem is None:
        raise HTTPException(status_code=404)
    return problem
