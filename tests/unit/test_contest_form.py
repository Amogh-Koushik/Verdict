from dataclasses import replace
from datetime import UTC, datetime

import pytest

from verdict.core.models import Contest
from verdict.web.contest_form import ContestForm, ContestFormError

VALID = ContestForm(
    slug="weekly-1",
    title="Weekly contest 1",
    starts_at="2026-12-01T10:00",
    ends_at="2026-12-01T12:30",
)


def test_times_are_read_as_ist_and_stored_as_utc() -> None:
    contest = Contest()
    VALID.apply_to(contest)
    assert contest.slug == "weekly-1"
    assert contest.title == "Weekly contest 1"
    assert contest.starts_at == datetime(2026, 12, 1, 4, 30, tzinfo=UTC)
    assert contest.ends_at == datetime(2026, 12, 1, 7, 0, tzinfo=UTC)


def test_a_saved_contest_fills_the_form_in_ist() -> None:
    contest = Contest(
        slug="weekly-1",
        title="Weekly contest 1",
        starts_at=datetime(2026, 12, 1, 4, 30, tzinfo=UTC),
        ends_at=datetime(2026, 12, 1, 7, 0, tzinfo=UTC),
    )
    assert ContestForm.from_contest(contest) == VALID


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param({"slug": "Weekly 1"}, "slug", id="slug with capitals and a space"),
        pytest.param({"slug": "a" * 65}, "slug", id="slug too long"),
        pytest.param({"title": ""}, "title", id="empty title"),
        pytest.param({"starts_at": ""}, "Start must be", id="missing start"),
        pytest.param({"ends_at": "1 December"}, "End must be", id="end not a date and time"),
        pytest.param(
            {"ends_at": "2026-12-01T10:00"}, "end after it starts", id="ends at the start"
        ),
        pytest.param(
            {"ends_at": "2026-11-30T10:00"}, "end after it starts", id="ends before it starts"
        ),
    ],
)
def test_an_invalid_form_changes_nothing(changes: dict[str, str], message: str) -> None:
    contest = Contest(slug="old", title="Old")
    with pytest.raises(ContestFormError, match=message):
        replace(VALID, **changes).apply_to(contest)
    assert (contest.slug, contest.title, contest.starts_at) == ("old", "Old", None)
