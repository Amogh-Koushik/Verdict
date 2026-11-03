import math
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import Row, select
from sqlalchemy.orm import Session

from verdict.core.config import Settings
from verdict.core.db import database_now
from verdict.core.events import publish_submission_changed
from verdict.core.models import Contest, ContestPhase, ContestProblem, Problem, User
from verdict.web.csrf import CSRF_PROTECTED
from verdict.web.deps import DbDep, RedisDep, SettingsDep, ViewerDep
from verdict.web.problems import LANGUAGE_NAMES, problem_page_context
from verdict.web.scoreboard_cache import contest_scoreboard
from verdict.web.submitting import (
    SubmissionFormDep,
    SubmissionRefusedError,
    accept_submission,
    refusal_page,
)
from verdict.web.templating import render

router = APIRouter()


@router.get("/contests")
def contest_list(request: Request, viewer: ViewerDep, db: DbDep) -> HTMLResponse:
    now = database_now(db)
    contests = db.scalars(select(Contest).order_by(Contest.starts_at, Contest.id)).all()
    by_phase = {
        phase: [contest for contest in contests if contest.phase_at(now) is phase]
        for phase in ContestPhase
    }
    context = {
        "running": by_phase[ContestPhase.RUNNING],
        "upcoming": by_phase[ContestPhase.UPCOMING],
        "ended": by_phase[ContestPhase.ENDED][::-1],
    }
    return render(request, viewer, "contests/list.html", context)


@router.get("/contests/{slug}")
def contest_page(request: Request, slug: str, viewer: ViewerDep, db: DbDep) -> HTMLResponse:
    contest = _contest_or_404(db, slug)
    now = database_now(db)
    phase = contest.phase_at(now)
    shows_problems = phase is not ContestPhase.UPCOMING or _is_admin(viewer.user)
    context = {
        "contest": contest,
        "phase": phase,
        "seconds_left": _seconds_to_next_phase(contest, now),
        "contest_problems": _contest_problems(db, contest) if shows_problems else [],
        "languages": LANGUAGE_NAMES,
        "submit_url": f"/contests/{contest.slug}/submit",
    }
    return render(request, viewer, "contests/view.html", context)


@router.get("/contests/{slug}/problems/{label}")
def contest_problem_page(
    request: Request, slug: str, label: str, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> HTMLResponse:
    contest = _contest_or_404(db, slug)
    now = database_now(db)
    problem = _contest_problem_or_404(db, viewer.user, contest, label, now)
    context = _contest_problem_context(contest, label, problem, now, settings)
    return render(request, viewer, "problems/view.html", context)


@router.post("/contests/{slug}/submit", dependencies=CSRF_PROTECTED)
def submit_to_contest(
    request: Request,
    slug: str,
    viewer: ViewerDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    form: SubmissionFormDep,
    label: Annotated[str, Form()] = "",
) -> Response:
    if viewer.user is None:
        return RedirectResponse("/login", status_code=303)
    contest = _contest_or_404(db, slug)
    # The submission is inserted in this same transaction, so its created_at equals `now`.
    now = database_now(db)
    problem = _contest_problem_or_404(db, viewer.user, contest, label, now)
    # Only a submission made inside the window counts for the contest; others are practice.
    contest_id = contest.id if contest.phase_at(now) is ContestPhase.RUNNING else None
    try:
        submission = accept_submission(
            db, redis, settings, form, user=viewer.user, problem=problem, contest_id=contest_id
        )
    except SubmissionRefusedError as refusal:
        page_context = _contest_problem_context(contest, label, problem, now, settings)
        return refusal_page(request, viewer, page_context, form, refusal)
    if contest_id is not None:
        # The scoreboard shows the new attempt as still being judged.
        publish_submission_changed(redis, submission.id, contest_id)
    return RedirectResponse(f"/submissions/{submission.id}", status_code=303)


@router.get("/contests/{slug}/scoreboard")
def scoreboard_page(
    request: Request, slug: str, viewer: ViewerDep, db: DbDep, redis: RedisDep
) -> HTMLResponse:
    contest = _contest_or_404(db, slug)
    now = database_now(db)
    phase = contest.phase_at(now)
    started = phase is not ContestPhase.UPCOMING
    context = {
        "contest": contest,
        "phase": phase,
        "seconds_left": _seconds_to_next_phase(contest, now),
        "scoreboard": contest_scoreboard(db, redis, contest) if started else None,
    }
    return render(request, viewer, "contests/scoreboard.html", context)


@router.get("/contests/{slug}/scoreboard/table")
def scoreboard_table(
    request: Request, slug: str, viewer: ViewerDep, db: DbDep, redis: RedisDep
) -> HTMLResponse:
    """The scoreboard table alone, re-fetched by the page whenever the contest changes."""
    contest = _contest_or_404(db, slug)
    if contest.phase_at(database_now(db)) is ContestPhase.UPCOMING:
        raise HTTPException(status_code=404)
    context = {"contest": contest, "scoreboard": contest_scoreboard(db, redis, contest)}
    return render(request, viewer, "contests/_scoreboard_table.html", context)


def _contest_or_404(db: Session, slug: str) -> Contest:
    contest = db.scalars(select(Contest).where(Contest.slug == slug)).one_or_none()
    if contest is None:
        raise HTTPException(status_code=404)
    return contest


def _contest_problem_or_404(
    db: Session, user: User | None, contest: Contest, label: str, now: datetime
) -> Problem:
    # Until the contest starts, its problems are hidden from everyone but administrators.
    if contest.phase_at(now) is ContestPhase.UPCOMING and not _is_admin(user):
        raise HTTPException(status_code=404)
    problem = db.scalars(
        select(Problem)
        .join(ContestProblem, ContestProblem.problem_id == Problem.id)
        .where(ContestProblem.contest_id == contest.id, ContestProblem.label == label)
    ).one_or_none()
    if problem is None:
        raise HTTPException(status_code=404)
    return problem


def _contest_problems(db: Session, contest: Contest) -> Sequence[Row[tuple[str, Problem]]]:
    return db.execute(
        select(ContestProblem.label, Problem)
        .join(Problem, Problem.id == ContestProblem.problem_id)
        .where(ContestProblem.contest_id == contest.id)
        .order_by(ContestProblem.label)
    ).all()


def _contest_problem_context(
    contest: Contest, label: str, problem: Problem, now: datetime, settings: Settings
) -> dict[str, Any]:
    phase = contest.phase_at(now)
    running = phase is ContestPhase.RUNNING
    return {
        **problem_page_context(problem, settings),
        "contest": contest,
        "label": label,
        "phase": phase,
        # The page counts down to the end only; before the start only admins can see it.
        "seconds_left": _seconds_to_next_phase(contest, now) if running else None,
        "submit_url": f"/contests/{contest.slug}/submit",
    }


def _seconds_to_next_phase(contest: Contest, now: datetime) -> int | None:
    """Seconds until the contest starts or ends, rounded up so a countdown never ends early."""
    phase = contest.phase_at(now)
    if phase is ContestPhase.ENDED:
        return None
    boundary = contest.starts_at if phase is ContestPhase.UPCOMING else contest.ends_at
    return math.ceil((boundary - now).total_seconds())


def _is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin
