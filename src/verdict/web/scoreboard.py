from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from verdict.core.verdicts import Verdict

PENALTY_MINUTES_PER_REJECTION = 20
# Compilation and system errors aren't wrong answers, so they cost nothing.
REJECTED_VERDICTS = frozenset(
    {
        Verdict.WRONG_ANSWER,
        Verdict.TIME_LIMIT_EXCEEDED,
        Verdict.MEMORY_LIMIT_EXCEEDED,
        Verdict.RUNTIME_ERROR,
    }
)


@dataclass(frozen=True)
class ContestSubmission:
    user_id: int
    username: str
    label: str
    submitted_at: datetime
    # None while the submission waits for, or is in, a judge.
    verdict: Verdict | None


@dataclass(frozen=True)
class Cell:
    """One contestant's result on one problem."""

    rejected: int = 0
    accepted_minute: int | None = None
    pending: bool = False

    @property
    def accepted(self) -> bool:
        return self.accepted_minute is not None

    @property
    def penalty(self) -> int:
        if self.accepted_minute is None:
            return 0
        return self.accepted_minute + PENALTY_MINUTES_PER_REJECTION * self.rejected

    @property
    def mark(self) -> str:
        """ "+" or "+2" once accepted, "?" while judging, "-3" after rejections only."""
        if self.accepted_minute is not None:
            return f"+{self.rejected or ''}"
        if self.pending:
            return "?"
        return f"-{self.rejected}" if self.rejected else ""


@dataclass(frozen=True)
class ScoreboardRow:
    rank: int
    user_id: int
    username: str
    solved: int
    penalty: int
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class Scoreboard:
    labels: tuple[str, ...]
    rows: tuple[ScoreboardRow, ...]


def build_scoreboard(
    labels: Sequence[str],
    starts_at: datetime,
    ends_at: datetime,
    submissions: Iterable[ContestSubmission],
) -> Scoreboard:
    """Rank by problems solved, then by penalty, under ICPC rules; ties share a rank."""
    attempts: dict[tuple[int, str], list[ContestSubmission]] = defaultdict(list)
    usernames: dict[int, str] = {}
    # A stable sort keeps the caller's order for submissions made at the same instant.
    for submission in sorted(submissions, key=lambda each: each.submitted_at):
        if submission.label in labels and starts_at <= submission.submitted_at < ends_at:
            attempts[submission.user_id, submission.label].append(submission)
            usernames[submission.user_id] = submission.username
    rows = [
        _unranked_row(
            user_id, username, [_cell(attempts[user_id, label], starts_at) for label in labels]
        )
        for user_id, username in usernames.items()
    ]
    return Scoreboard(labels=tuple(labels), rows=_ranked(rows))


def _cell(attempts: list[ContestSubmission], starts_at: datetime) -> Cell:
    rejected = 0
    for attempt in attempts:
        if attempt.verdict is None:
            # Until this verdict is known, it isn't known which later attempts count.
            return Cell(rejected=rejected, pending=True)
        if attempt.verdict is Verdict.ACCEPTED:
            # Attempts after the first accepted one are ignored.
            minute = (attempt.submitted_at - starts_at) // timedelta(minutes=1)
            return Cell(rejected=rejected, accepted_minute=minute)
        if attempt.verdict in REJECTED_VERDICTS:
            rejected += 1
    return Cell(rejected=rejected)


def _unranked_row(user_id: int, username: str, cells: list[Cell]) -> ScoreboardRow:
    return ScoreboardRow(
        rank=0,
        user_id=user_id,
        username=username,
        solved=sum(cell.accepted for cell in cells),
        penalty=sum(cell.penalty for cell in cells),
        cells=tuple(cells),
    )


def _ranked(rows: list[ScoreboardRow]) -> tuple[ScoreboardRow, ...]:
    ordered = sorted(
        rows, key=lambda row: (-row.solved, row.penalty, row.username.casefold(), row.user_id)
    )
    ranked: list[ScoreboardRow] = []
    for position, row in enumerate(ordered, start=1):
        previous = ranked[-1] if ranked else None
        if previous and (previous.solved, previous.penalty) == (row.solved, row.penalty):
            ranked.append(replace(row, rank=previous.rank))
        else:
            ranked.append(replace(row, rank=position))
    return tuple(ranked)
