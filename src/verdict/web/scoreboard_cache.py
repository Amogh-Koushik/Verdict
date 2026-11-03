import json
import logging
from dataclasses import asdict
from typing import cast

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from verdict.core.events import scoreboard_generation_key, scoreboard_key
from verdict.core.models import Contest, ContestProblem, Submission, User
from verdict.core.verdicts import SubmissionStatus
from verdict.web.scoreboard import (
    Cell,
    ContestSubmission,
    Scoreboard,
    ScoreboardRow,
    build_scoreboard,
)

logger = logging.getLogger(__name__)

# Entries are deleted whenever the contest changes. The expiry bounds how long one outlives a
# change whose invalidation failed, and clears out finished contests.
CACHE_SECONDS = 10 * 60
PENDING_STATUSES = (SubmissionStatus.QUEUED, SubmissionStatus.JUDGING)


def contest_scoreboard(db: Session, redis: Redis, contest: Contest) -> Scoreboard:
    """The contest's scoreboard from the Redis cache, computed from Postgres when it isn't there."""
    try:
        keys = (scoreboard_generation_key(contest.id), scoreboard_key(contest.id))
        # The client decodes responses, so values arrive as str.
        generation, cached = cast(list[str | None], redis.mget(keys))
    except RedisError:
        logger.warning(
            "scoreboard cache unreadable", exc_info=True, extra={"contest_id": contest.id}
        )
        return compute_scoreboard(db, contest)
    # Read before Postgres, so the entry stored below is tagged with a change it has seen.
    current = generation or "0"
    if cached is not None:
        cached_generation, scoreboard = _decode(cached)
        if cached_generation == current:
            return scoreboard
    scoreboard = compute_scoreboard(db, contest)
    try:
        redis.set(scoreboard_key(contest.id), _encode(scoreboard, current), ex=CACHE_SECONDS)
    except RedisError:
        logger.warning("scoreboard not cached", exc_info=True, extra={"contest_id": contest.id})
    return scoreboard


def compute_scoreboard(db: Session, contest: Contest) -> Scoreboard:
    labels = db.scalars(
        select(ContestProblem.label)
        .where(ContestProblem.contest_id == contest.id)
        .order_by(ContestProblem.label)
    ).all()
    rows = db.execute(
        select(
            Submission.user_id,
            User.username,
            ContestProblem.label,
            Submission.created_at,
            Submission.status,
            Submission.verdict,
        )
        .join(User, User.id == Submission.user_id)
        .join(
            ContestProblem,
            and_(
                ContestProblem.contest_id == Submission.contest_id,
                ContestProblem.problem_id == Submission.problem_id,
            ),
        )
        .where(Submission.contest_id == contest.id)
        .order_by(Submission.created_at, Submission.id)
    ).all()
    submissions = [
        ContestSubmission(
            user_id=row.user_id,
            username=row.username,
            label=row.label,
            submitted_at=row.created_at,
            # The status, not the verdict column, says whether judging has finished.
            verdict=None if row.status in PENDING_STATUSES else row.verdict,
        )
        for row in rows
    ]
    return build_scoreboard(labels, contest.starts_at, contest.ends_at, submissions)


def _encode(scoreboard: Scoreboard, generation: str) -> str:
    return json.dumps({"generation": generation, "scoreboard": asdict(scoreboard)})


def _decode(raw: str) -> tuple[str, Scoreboard]:
    entry = json.loads(raw)
    rows = tuple(
        ScoreboardRow(**{**row, "cells": tuple(Cell(**cell) for cell in row["cells"])})
        for row in entry["scoreboard"]["rows"]
    )
    labels = tuple(entry["scoreboard"]["labels"])
    return entry["generation"], Scoreboard(labels=labels, rows=rows)
