from datetime import UTC, datetime, timedelta

import pytest

from verdict.core.verdicts import Verdict
from verdict.web.scoreboard import ContestSubmission, build_scoreboard

START = datetime(2026, 11, 3, 4, 30, tzinfo=UTC)
END = START + timedelta(hours=2)
USER_IDS = {"alice": 1, "bob": 2, "Carol": 3, "dave": 4}

AC = Verdict.ACCEPTED
WA = Verdict.WRONG_ANSWER
TLE = Verdict.TIME_LIMIT_EXCEEDED
MLE = Verdict.MEMORY_LIMIT_EXCEEDED
RE = Verdict.RUNTIME_ERROR
CE = Verdict.COMPILATION_ERROR
SE = Verdict.SYSTEM_ERROR
JUDGING = None


def submission(
    username: str, label: str, minute: float, verdict: Verdict | None
) -> ContestSubmission:
    return ContestSubmission(
        user_id=USER_IDS[username],
        username=username,
        label=label,
        submitted_at=START + timedelta(minutes=minute),
        verdict=verdict,
    )


@pytest.mark.parametrize(
    ("attempts", "mark", "solved", "penalty"),
    [
        pytest.param([(17, AC)], "+", 1, 17, id="accepted first time costs the minutes taken"),
        pytest.param(
            [(5, WA), (40, AC)], "+1", 1, 60, id="wrong attempt before accept adds twenty minutes"
        ),
        pytest.param(
            [(1, WA), (2, TLE), (3, MLE), (4, RE), (50, AC)],
            "+4",
            1,
            130,
            id="wrong answer, time, memory and runtime errors are all rejections",
        ),
        pytest.param([(5, CE), (9, AC)], "+", 1, 9, id="compilation error is not penalised"),
        pytest.param([(5, SE), (9, AC)], "+", 1, 9, id="system error is not penalised"),
        pytest.param(
            [(30, AC), (31, WA), (32, AC)],
            "+",
            1,
            30,
            id="submissions after the first accept are ignored",
        ),
        pytest.param(
            [(30, AC), (31, JUDGING)], "+", 1, 30, id="judging after the first accept is ignored"
        ),
        pytest.param(
            [(40, AC), (5, WA)], "+1", 1, 60, id="attempts are taken in time order, not list order"
        ),
        pytest.param([(59.9, AC)], "+", 1, 59, id="minutes are rounded down"),
        pytest.param(
            [(3, WA), (5, WA), (8, RE)], "-3", 0, 0, id="rejections without an accept cost nothing"
        ),
        pytest.param([(3, CE)], "", 0, 0, id="only compilation errors leave the cell empty"),
        pytest.param(
            [(10, JUDGING)], "?", 0, 0, id="an attempt being judged shows a question mark"
        ),
        pytest.param(
            [(10, WA), (12, JUDGING), (15, AC)],
            "?",
            0,
            0,
            id="an accept after an attempt still being judged is not counted yet",
        ),
    ],
)
def test_a_problem_cell_follows_icpc_rules(
    attempts: list[tuple[float, Verdict | None]], mark: str, solved: int, penalty: int
) -> None:
    submissions = [submission("alice", "A", minute, verdict) for minute, verdict in attempts]
    [row] = build_scoreboard(["A"], START, END, submissions).rows
    [cell] = row.cells
    assert cell.mark == mark
    assert (row.solved, row.penalty) == (solved, penalty)


def test_an_accepted_cell_keeps_the_minute_it_was_solved() -> None:
    submissions = [submission("alice", "A", 5, WA), submission("alice", "A", 47.5, AC)]
    [row] = build_scoreboard(["A"], START, END, submissions).rows
    assert row.cells[0].accepted_minute == 47


def test_penalty_adds_up_over_every_solved_problem() -> None:
    submissions = [
        submission("alice", "A", 5, WA),
        submission("alice", "A", 40, AC),
        submission("alice", "B", 70, AC),
        submission("alice", "C", 80, WA),
    ]
    [row] = build_scoreboard(["A", "B", "C"], START, END, submissions).rows
    assert [cell.mark for cell in row.cells] == ["+1", "+", "-1"]
    assert (row.solved, row.penalty) == (2, 60 + 70)


@pytest.mark.parametrize(
    ("submitted_at", "counted"),
    [
        pytest.param(START - timedelta(seconds=1), False, id="a second before the start"),
        pytest.param(START, True, id="at the start"),
        pytest.param(END - timedelta(seconds=1), True, id="a second before the end"),
        pytest.param(END, False, id="at the end"),
        pytest.param(END + timedelta(days=1), False, id="a day after the end"),
    ],
)
def test_only_submissions_made_inside_the_window_count(
    submitted_at: datetime, counted: bool
) -> None:
    accepted = ContestSubmission(1, "alice", "A", submitted_at, AC)
    rows = build_scoreboard(["A"], START, END, [accepted]).rows
    assert [row.solved for row in rows] == ([1] if counted else [])


def test_a_rejection_outside_the_window_adds_no_penalty() -> None:
    submissions = [
        ContestSubmission(1, "alice", "A", START - timedelta(minutes=1), WA),
        submission("alice", "A", 10, AC),
        ContestSubmission(1, "alice", "B", END, WA),
    ]
    [row] = build_scoreboard(["A", "B"], START, END, submissions).rows
    assert [cell.mark for cell in row.cells] == ["+", ""]
    assert row.penalty == 10


def test_a_submission_to_a_problem_no_longer_in_the_contest_is_ignored() -> None:
    submissions = [submission("alice", "A", 10, AC), submission("bob", "Z", 5, AC)]
    rows = build_scoreboard(["A"], START, END, submissions).rows
    assert [row.username for row in rows] == ["alice"]


@pytest.mark.parametrize(
    ("submissions", "standings"),
    [
        pytest.param(
            [
                submission("alice", "A", 10, AC),
                submission("bob", "A", 5, AC),
                submission("bob", "B", 100, AC),
            ],
            [(1, "bob", 2, 105), (2, "alice", 1, 10)],
            id="more problems solved beats a lower penalty",
        ),
        pytest.param(
            [submission("alice", "A", 30, AC), submission("bob", "A", 20, AC)],
            [(1, "bob", 1, 20), (2, "alice", 1, 30)],
            id="with equal problems solved the lower penalty ranks higher",
        ),
        pytest.param(
            [
                submission("dave", "A", 50, AC),
                submission("Carol", "A", 20, AC),
                submission("alice", "A", 20, AC),
                submission("bob", "A", 1, WA),
            ],
            [(1, "alice", 1, 20), (1, "Carol", 1, 20), (3, "dave", 1, 50), (4, "bob", 0, 0)],
            id="ties share a rank and the next rank skips past them",
        ),
        pytest.param(
            [
                submission("bob", "A", 1, WA),
                submission("alice", "A", 2, CE),
                submission("Carol", "B", 3, JUDGING),
            ],
            [(1, "alice", 0, 0), (1, "bob", 0, 0), (1, "Carol", 0, 0)],
            id="everyone with nothing solved shares first place",
        ),
        pytest.param(
            [
                submission("alice", "A", 10, WA),
                submission("alice", "A", 30, AC),
                submission("bob", "A", 50, AC),
            ],
            [(1, "alice", 1, 50), (1, "bob", 1, 50)],
            id="equal penalty made up differently is still a tie",
        ),
    ],
)
def test_contestants_are_ranked_by_problems_solved_then_penalty(
    submissions: list[ContestSubmission], standings: list[tuple[int, str, int, int]]
) -> None:
    rows = build_scoreboard(["A", "B"], START, END, submissions).rows
    assert [(row.rank, row.username, row.solved, row.penalty) for row in rows] == standings


def test_cells_follow_the_order_of_the_contests_labels() -> None:
    submissions = [submission("alice", "C", 5, AC), submission("alice", "A", 9, WA)]
    scoreboard = build_scoreboard(["A", "B", "C"], START, END, submissions)
    assert scoreboard.labels == ("A", "B", "C")
    assert [cell.mark for cell in scoreboard.rows[0].cells] == ["-1", "", "+"]


def test_a_contest_without_submissions_has_an_empty_scoreboard() -> None:
    scoreboard = build_scoreboard(["A"], START, END, [])
    assert scoreboard.rows == ()
