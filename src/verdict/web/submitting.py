import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, BinaryIO

from fastapi import Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from redis import Redis
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from verdict.core.config import Settings
from verdict.core.events import QUEUE_CHANNEL
from verdict.core.models import MAX_SOURCE_BYTES, Problem, Submission, User
from verdict.core.verdicts import Language, QueuePriority, SubmissionStatus
from verdict.web.deps import Viewer
from verdict.web.rate_limit import RateLimitExceededError, hit_fixed_window
from verdict.web.templating import render

logger = logging.getLogger(__name__)

# (submissions allowed, window length in seconds), checked in this order.
SUBMIT_RATE_LIMITS = [(1, 10), (60, 60 * 60)]
QUEUE_FULL_RETRY_SECONDS = 30


class SubmissionRefusedError(Exception):
    def __init__(
        self, message: str, status_code: int, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers


@dataclass(frozen=True)
class SubmissionForm:
    language: str
    source: str
    upload: BinaryIO | None


FormField = Annotated[str, Form()]


def read_submission_form(
    language: FormField = "",
    source: FormField = "",
    source_file: Annotated[UploadFile | None, File()] = None,
) -> SubmissionForm:
    # Browsers send an empty file part when no file was chosen.
    upload = source_file.file if source_file is not None and source_file.filename else None
    return SubmissionForm(language=language, source=source, upload=upload)


SubmissionFormDep = Annotated[SubmissionForm, Depends(read_submission_form)]


def accept_submission(
    db: Session,
    redis: Redis,
    settings: Settings,
    form: SubmissionForm,
    *,
    user: User,
    problem: Problem,
    contest_id: int | None,
) -> Submission:
    """Check a submission against every limit and queue it, or raise SubmissionRefusedError."""
    chosen_language = _parse_language(form.language)
    source = _read_source(form.source, form.upload)
    _check_can_queue(db, redis, settings, user, problem)
    submission = queue_submission(db, user, problem, chosen_language, source, contest_id)
    logger.info(
        "submission queued",
        extra={
            "submission_id": submission.id,
            "user_id": user.id,
            "problem_id": problem.id,
            "contest_id": contest_id,
        },
    )
    return submission


def queue_submission(
    db: Session,
    user: User,
    problem: Problem,
    language: Language,
    source: str,
    contest_id: int | None,
) -> Submission:
    """Insert a queued submission and wake a worker; NOTIFY is delivered when the insert commits."""
    submission = Submission(
        user_id=user.id,
        problem_id=problem.id,
        contest_id=contest_id,
        language=language,
        source=source,
    )
    db.add(submission)
    db.flush()
    db.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": QUEUE_CHANNEL, "payload": str(submission.id)},
    )
    db.commit()
    return submission


def refusal_page(
    request: Request,
    viewer: Viewer,
    page_context: Mapping[str, Any],
    form: SubmissionForm,
    refusal: SubmissionRefusedError,
) -> HTMLResponse:
    """The problem page again, with the reason shown and the pasted code kept in the form."""
    context = {
        **page_context,
        "error": str(refusal),
        "source": form.source,
        "chosen_language": form.language,
    }
    return render(
        request,
        viewer,
        "problems/view.html",
        context,
        status_code=refusal.status_code,
        headers=refusal.headers,
    )


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
    # Rejudged submissions wait behind new ones, so they don't count toward the limit: a large
    # rejudge must not lock contestants out.
    queued = db.scalar(
        select(func.count())
        .select_from(Submission)
        .where(
            Submission.status == SubmissionStatus.QUEUED,
            Submission.priority == QueuePriority.NEW,
        )
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
