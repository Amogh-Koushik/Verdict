from datetime import UTC, datetime, timedelta

import pytest

from verdict.core.models import Contest, ContestPhase

START = datetime(2026, 12, 1, 4, 30, tzinfo=UTC)
END = START + timedelta(hours=2)


@pytest.mark.parametrize(
    ("now", "phase"),
    [
        pytest.param(
            START - timedelta(microseconds=1), ContestPhase.UPCOMING, id="just before the start"
        ),
        pytest.param(START, ContestPhase.RUNNING, id="at the start"),
        pytest.param(
            END - timedelta(microseconds=1), ContestPhase.RUNNING, id="just before the end"
        ),
        pytest.param(END, ContestPhase.ENDED, id="at the end"),
    ],
)
def test_the_window_includes_its_start_and_excludes_its_end(
    now: datetime, phase: ContestPhase
) -> None:
    contest = Contest(starts_at=START, ends_at=END)
    assert contest.phase_at(now) is phase
