import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from verdict.core.models import Problem, ProblemTest
from verdict.web.csrf import CSRF_PROTECTED
from verdict.web.deps import DbDep, SettingsDep, Viewer, ViewerDep, require_admin
from verdict.web.problem_form import ProblemForm, ProblemFormDep, ProblemFormError
from verdict.web.templating import render
from verdict.web.test_archive import MAX_TESTS, TestArchiveError, store_test_archive

logger = logging.getLogger(__name__)

# Declared on the router so no admin route can be added without the check.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/problems")
def problem_index(request: Request, viewer: ViewerDep, db: DbDep) -> HTMLResponse:
    problems = db.scalars(select(Problem).order_by(Problem.id)).all()
    return render(request, viewer, "admin/problems.html", {"problems": problems})


@router.get("/problems/new")
def new_problem(request: Request, viewer: ViewerDep) -> HTMLResponse:
    return _problem_form_page(request, viewer, ProblemForm(), problem=None)


@router.post("/problems/new", dependencies=CSRF_PROTECTED)
def create_problem(
    request: Request, viewer: ViewerDep, db: DbDep, form: ProblemFormDep
) -> Response:
    return _save_problem(request, viewer, db, form, Problem())


@router.get("/problems/{problem_id}/edit")
def edit_problem(request: Request, problem_id: int, viewer: ViewerDep, db: DbDep) -> HTMLResponse:
    problem = _problem_or_404(db, problem_id)
    return _problem_form_page(request, viewer, ProblemForm.from_problem(problem), problem)


@router.post("/problems/{problem_id}/edit", dependencies=CSRF_PROTECTED)
def update_problem(
    request: Request, problem_id: int, viewer: ViewerDep, db: DbDep, form: ProblemFormDep
) -> Response:
    return _save_problem(request, viewer, db, form, _problem_or_404(db, problem_id))


@router.get("/problems/{problem_id}/tests")
def problem_tests(request: Request, problem_id: int, viewer: ViewerDep, db: DbDep) -> HTMLResponse:
    return _tests_page(request, viewer, db, _problem_or_404(db, problem_id))


@router.post("/problems/{problem_id}/tests", dependencies=CSRF_PROTECTED)
def upload_tests(
    request: Request,
    problem_id: int,
    viewer: ViewerDep,
    db: DbDep,
    settings: SettingsDep,
    archive: Annotated[UploadFile | None, File()] = None,
) -> Response:
    problem = _problem_or_404(db, problem_id)
    if archive is None or not archive.filename:
        return _tests_page(request, viewer, db, problem, "Choose a zip file to upload.", 400)
    try:
        version = store_test_archive(db, problem.id, archive.file, settings.problems_dir)
    except TestArchiveError as error:
        logger.info("test archive rejected", extra={"problem_id": problem.id, "reason": str(error)})
        return _tests_page(request, viewer, db, problem, str(error), 400)
    logger.info("tests uploaded", extra={"problem_id": problem.id, "tests_version": version})
    return RedirectResponse(f"/admin/problems/{problem.id}/tests", status_code=303)


def _problem_or_404(db: Session, problem_id: int) -> Problem:
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404)
    return problem


def _save_problem(
    request: Request, viewer: Viewer, db: Session, form: ProblemForm, problem: Problem
) -> Response:
    existing = problem if problem.id is not None else None

    def form_error(message: str, status_code: int) -> Response:
        return _problem_form_page(request, viewer, form, existing, message, status_code)

    try:
        form.apply_to(problem)
    except ProblemFormError as error:
        return form_error(str(error), 400)
    db.add(problem)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if not _is_slug_conflict(error):
            raise
        return form_error(f"Another problem already uses the slug {form.slug!r}.", 409)
    if existing is None:
        logger.info("problem created", extra={"problem_id": problem.id})
        return RedirectResponse(f"/admin/problems/{problem.id}/tests", status_code=303)
    logger.info("problem updated", extra={"problem_id": problem.id})
    return RedirectResponse("/admin/problems", status_code=303)


def _problem_form_page(
    request: Request,
    viewer: Viewer,
    form: ProblemForm,
    problem: Problem | None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = {"form": form, "problem": problem, "error": error}
    return render(request, viewer, "admin/problem_form.html", context, status_code=status_code)


def _tests_page(
    request: Request,
    viewer: Viewer,
    db: Session,
    problem: Problem,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    tests = db.scalars(
        select(ProblemTest)
        .where(ProblemTest.problem_id == problem.id, ProblemTest.version == problem.tests_version)
        .order_by(ProblemTest.idx)
    ).all()
    context = {"problem": problem, "tests": tests, "error": error, "max_tests": MAX_TESTS}
    return render(request, viewer, "admin/problem_tests.html", context, status_code=status_code)


def _is_slug_conflict(error: IntegrityError) -> bool:
    return (
        isinstance(error.orig, UniqueViolation)
        and error.orig.diag.constraint_name == "uq_problems_slug"
    )
