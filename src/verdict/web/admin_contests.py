import logging
import string
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from redis import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from verdict.core.events import publish_contest_changed
from verdict.core.models import Contest, ContestProblem, Problem
from verdict.web.contest_form import ContestForm, ContestFormDep, ContestFormError
from verdict.web.csrf import CSRF_PROTECTED
from verdict.web.deps import DbDep, RedisDep, Viewer, ViewerDep, require_admin
from verdict.web.templating import render

logger = logging.getLogger(__name__)

# Declared on the router so no admin route can be added without the check.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

LABELS = tuple(string.ascii_uppercase)
# The database enforces these, so two admins editing at once can't break them either.
ATTACH_CONFLICTS = {
    "pk_contest_problems": "Label {label} is already used in this contest.",
    "uq_contest_problems_contest_id_problem_id": "That problem is already in this contest.",
    "fk_contest_problems_problem_id_problems": "That problem doesn't exist.",
}

FormField = Annotated[str, Form()]


@router.get("/contests")
def contest_index(request: Request, viewer: ViewerDep, db: DbDep) -> HTMLResponse:
    contests = db.scalars(select(Contest).order_by(Contest.starts_at.desc(), Contest.id)).all()
    return render(request, viewer, "admin/contests.html", {"contests": contests})


@router.get("/contests/new")
def new_contest(request: Request, viewer: ViewerDep, db: DbDep) -> HTMLResponse:
    return _contest_page(request, viewer, db, None, ContestForm())


@router.post("/contests/new", dependencies=CSRF_PROTECTED)
def create_contest(
    request: Request, viewer: ViewerDep, db: DbDep, redis: RedisDep, form: ContestFormDep
) -> Response:
    return _save_contest(request, viewer, db, redis, form, Contest())


@router.get("/contests/{contest_id}/edit")
def edit_contest(request: Request, contest_id: int, viewer: ViewerDep, db: DbDep) -> HTMLResponse:
    contest = _contest_or_404(db, contest_id)
    return _contest_page(request, viewer, db, contest, ContestForm.from_contest(contest))


@router.post("/contests/{contest_id}/edit", dependencies=CSRF_PROTECTED)
def update_contest(
    request: Request,
    contest_id: int,
    viewer: ViewerDep,
    db: DbDep,
    redis: RedisDep,
    form: ContestFormDep,
) -> Response:
    return _save_contest(request, viewer, db, redis, form, _contest_or_404(db, contest_id))


@router.post("/contests/{contest_id}/problems", dependencies=CSRF_PROTECTED)
def add_problem(
    request: Request,
    contest_id: int,
    viewer: ViewerDep,
    db: DbDep,
    redis: RedisDep,
    label: FormField = "",
    problem_id: FormField = "",
) -> Response:
    contest = _contest_or_404(db, contest_id)

    def refused(message: str, status_code: int) -> Response:
        form = ContestForm.from_contest(contest)
        return _contest_page(
            request, viewer, db, contest, form, problems_error=message, status_code=status_code
        )

    if label not in LABELS:
        return refused("Choose a label from A to Z.", 400)
    if not problem_id.isdigit():
        return refused("Choose a problem.", 400)
    db.add(ContestProblem(contest_id=contest.id, label=label, problem_id=int(problem_id)))
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        message = ATTACH_CONFLICTS.get(_constraint_name(error) or "")
        if message is None:
            raise
        return refused(message.format(label=label), 409)
    logger.info(
        "problem added to contest",
        extra={"contest_id": contest.id, "label": label, "problem_id": int(problem_id)},
    )
    return _contest_changed(redis, contest)


@router.post("/contests/{contest_id}/problems/{label}/remove", dependencies=CSRF_PROTECTED)
def remove_problem(contest_id: int, label: str, db: DbDep, redis: RedisDep) -> Response:
    contest = _contest_or_404(db, contest_id)
    entry = db.get(ContestProblem, (contest.id, label))
    if entry is None:
        raise HTTPException(status_code=404)
    db.delete(entry)
    db.commit()
    logger.info("problem removed from contest", extra={"contest_id": contest.id, "label": label})
    return _contest_changed(redis, contest)


def _contest_or_404(db: Session, contest_id: int) -> Contest:
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404)
    return contest


def _save_contest(
    request: Request, viewer: Viewer, db: Session, redis: Redis, form: ContestForm, contest: Contest
) -> Response:
    existing = contest if contest.id is not None else None

    def form_error(message: str, status_code: int) -> Response:
        return _contest_page(request, viewer, db, existing, form, message, status_code)

    try:
        form.apply_to(contest)
    except ContestFormError as error:
        return form_error(str(error), 400)
    db.add(contest)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if _constraint_name(error) != "uq_contests_slug":
            raise
        return form_error(f"Another contest already uses the slug {form.slug!r}.", 409)
    if existing is None:
        logger.info("contest created", extra={"contest_id": contest.id})
        return RedirectResponse(f"/admin/contests/{contest.id}/edit", status_code=303)
    logger.info("contest updated", extra={"contest_id": contest.id})
    return _contest_changed(redis, contest)


def _contest_changed(redis: Redis, contest: Contest) -> Response:
    # Its times or problems decide the scoreboard, so the cached one is out of date.
    publish_contest_changed(redis, contest.id)
    return RedirectResponse(f"/admin/contests/{contest.id}/edit", status_code=303)


def _contest_page(
    request: Request,
    viewer: Viewer,
    db: Session,
    contest: Contest | None,
    form: ContestForm,
    error: str | None = None,
    status_code: int = 200,
    problems_error: str | None = None,
) -> HTMLResponse:
    context: dict[str, object] = {
        "contest": contest,
        "form": form,
        "error": error,
        "problems_error": problems_error,
    }
    if contest is not None:
        attached = db.execute(
            select(ContestProblem.label, Problem)
            .join(Problem, Problem.id == ContestProblem.problem_id)
            .where(ContestProblem.contest_id == contest.id)
            .order_by(ContestProblem.label)
        ).all()
        used_labels = {label for label, _ in attached}
        attached_ids = {problem.id for _, problem in attached}
        problems = db.scalars(select(Problem).order_by(Problem.id)).all()
        context["attached"] = attached
        context["free_labels"] = [label for label in LABELS if label not in used_labels]
        context["available_problems"] = [p for p in problems if p.id not in attached_ids]
    return render(request, viewer, "admin/contest_form.html", context, status_code=status_code)


def _constraint_name(error: IntegrityError) -> str | None:
    if isinstance(error.orig, psycopg.Error) and error.orig.diag is not None:
        return error.orig.diag.constraint_name
    return None
