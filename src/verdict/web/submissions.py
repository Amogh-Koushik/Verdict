import logging
from typing import Annotated, BinaryIO

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from redis import Redis
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from verdict.core.config import Settings
from verdict.core.events import QUEUE_CHANNEL
from verdict.core.models import MAX_SOURCE_BYTES, Problem, Submission, User
from verdict.core.verdicts import Language, SubmissionStatus
from verdict.web.csrf import CSRF_PROTECTED
from verdict.web.deps import DbDep, RedisDep, SettingsDep, ViewerDep
from verdict.web.problems import problem_page_context
from verdict.web.rate_limit import RateLimitExceededError, hit_fixed_window
from verdict.web.templating import render
from verdict.web.visibility import visible_problems

logger = logging.getLogger(__name__)

router = APIRouter()

# (submissions allowed, window length in seconds), checked in this order.
SUBMIT_RATE_LIMITS = [(1, 10), (60, 60 * 60)]
QUEUE_FULL_RETRY_SECONDS = 30
RECENT_SUBMISSIONS = 50

FormField = Annotated[str, Form()]


class SubmissionRefusedError(Exception):
    def __init__(
        self, message: str, status_code: int, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers


@router.post("/problems/{slug}/submit", dependencies=CSRF_PROTECTED)
def submit(
    request: Request,
    slug: str,
    viewer: ViewerDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    language: FormField = "",
    source: FormField = "",
    source_file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    if viewer.user is None:
        return RedirectResponse("/login", status_code=303)
    problem = _visible_problem(db, viewer.user, slug)
    upload = source_file.file if source_file is not None and source_file.filename else None
    try:
        chosen_language = _parse_language(language)
        code = _read_source(source, upload)
        _check_can_queue(db, redis, settings, viewer.user, problem)
    except SubmissionRefusedError as refusal:
        context = {
            **problem_page_context(problem, settings),
            "error": str(refusal),
            "source": source,
            "chosen_language": language,
        }
        return render(
            request,
            viewer,
            "problems/view.html",
            context,
            status_code=refusal.status_code,
            headers=refusal.headers,
        )
    submission = queue_submission(db, viewer.user, problem, chosen_language, code)
    logger.info(
        "submission queued",
        extra={"submission_id": submission.id, "user_id": viewer.user.id, "problem_id": problem.id},
    )
    return RedirectResponse(f"/submissions/{submission.id}", status_code=303)


def queue_submission(
    db: Session, user: User, problem: Problem, language: Language, source: str
) -> Submission:
    """Insert a queued submission and wake a worker; NOTIFY is delivered when the insert commits."""
    submission = Submission(
        user_id=user.id, problem_id=problem.id, language=language, source=source
    )
    db.add(submission)
    db.flush()
    db.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": QUEUE_CHANNEL, "payload": str(submission.id)},
    )
    db.commit()
    return submission


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
    return render(request, viewer, "submissions/list.html", {"rows": rows})


@router.get("/submissions/{submission_id}")
def submission_page(
    request: Request, submission_id: int, viewer: ViewerDep, db: DbDep
) -> HTMLResponse:
    submission = db.get(Submission, submission_id)
    user = viewer.user
    # Only the author and administrators may see a submission and its source code.
    if submission is None or user is None or not (user.is_admin or submission.user_id == user.id):
        raise HTTPException(status_code=404)
    problem = db.get_one(Problem, submission.problem_id)
    context = {"submission": submission, "problem": problem}
    return render(request, viewer, "submissions/view.html", context)


def _visible_problem(db: Session, user: User, slug: str) -> Problem:
    problem = db.scalars(visible_problems(user).where(Problem.slug == slug)).one_or_none()
    if problem is None:
        raise HTTPException(status_code=404)
    return problem


def _parse_language(language: str) -> Language:
    try:
        return Language(language)
    except ValueError:
        raise SubmissionRefusedError("Choose C++ or Python.", 400) from None


def _read_source(pasted: str, upload: BinaryIO | None) -> str:
    if upload is not None:
        # One byte over the limit is enough to know the file is too large.
        raw = upload.read(MAX_SOURCE_BYTES + 1)
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SubmissionRefusedError("The file must be UTF-8 text.", 400) from None
    else:
        source = pasted
    if not source.strip():
        raise SubmissionRefusedError("Paste your solution or choose a file.", 400)
    if len(source.encode()) > MAX_SOURCE_BYTES:
        raise SubmissionRefusedError(
            f"Source code can be at most {MAX_SOURCE_BYTES // 1024} KB.", 413
        )
    if "\x00" in source:
        raise SubmissionRefusedError("Source code can't contain NUL characters.", 400)
    return source


def _check_can_queue(
    db: Session, redis: Redis, settings: Settings, user: User, problem: Problem
) -> None:
    if problem.tests_version == 0:
        raise SubmissionRefusedError("This problem has no tests yet, so it can't be judged.", 400)
    queued = db.scalar(
        select(func.count())
        .select_from(Submission)
        .where(Submission.status == SubmissionStatus.QUEUED)
    )
    # Checked before the rate limits, so a refusal here doesn't use up the user's allowance.
    if (queued or 0) >= settings.queue_limit:
        logger.warning("queue full; submission refused", extra={"queued": queued})
        raise SubmissionRefusedError(
            "The judge is busy right now. Please try again in a minute.",
            503,
            {"Retry-After": str(QUEUE_FULL_RETRY_SECONDS)},
        )
    for limit, window_seconds in SUBMIT_RATE_LIMITS:
        try:
            hit_fixed_window(redis, f"submit:{user.id}:{window_seconds}", limit, window_seconds)
        except RateLimitExceededError as error:
            wait = error.retry_after_seconds
            raise SubmissionRefusedError(
                f"You're submitting too quickly. Try again in {wait} seconds.",
                429,
                {"Retry-After": str(wait)},
            ) from None
