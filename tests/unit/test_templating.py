import pytest

from verdict.web.templating import format_clock


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [
        (1, "0:00:01"),
        (61, "0:01:01"),
        (3600, "1:00:00"),
        (2 * 86400 + 5, "48:00:05"),
    ],
)
def test_a_countdown_shows_hours_minutes_and_seconds(seconds: int, shown: str) -> None:
    assert format_clock(seconds) == shown
