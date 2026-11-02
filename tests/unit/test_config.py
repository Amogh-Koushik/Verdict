from pathlib import Path

import pytest

from verdict.core.config import ConfigError, Settings, load_settings


def test_empty_environment_gives_documented_defaults() -> None:
    assert load_settings({}) == Settings()
    assert Settings().lease_seconds == 60
    assert Settings().queue_limit == 500


def test_environment_values_override_defaults_with_their_types() -> None:
    settings = load_settings(
        {
            "DATABASE_URL": "postgresql+psycopg://u:p@db/x",
            "LOG_LEVEL": "debug",
            "PROBLEMS_DIR": "/srv/problems",
            "SESSION_COOKIE_SECURE": "true",
            "LEASE_SECONDS": "90",
            "POLL_SECONDS": "0.5",
            "PYTHON_TIME_MULTIPLIER": "3",
        }
    )
    assert settings.database_url == "postgresql+psycopg://u:p@db/x"
    assert settings.log_level == "DEBUG"
    assert settings.problems_dir == Path("/srv/problems")
    assert settings.session_cookie_secure is True
    assert settings.lease_seconds == 90
    assert settings.poll_seconds == 0.5
    assert settings.python_time_multiplier == 3.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LEASE_SECONDS", "sixty"),
        ("LEASE_SECONDS", "0"),
        ("MAX_ATTEMPTS", "-1"),
        ("POLL_SECONDS", "fast"),
        ("SESSION_COOKIE_SECURE", "maybe"),
    ],
)
def test_unparseable_or_non_positive_value_is_rejected(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_settings({name: value})
