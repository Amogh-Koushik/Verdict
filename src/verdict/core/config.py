import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """An environment variable is set to a value that can't be parsed."""


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg://verdict:verdict@localhost:5432/verdict"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    problems_dir: Path = Path("/data/problems")
    session_cookie_secure: bool = False
    lease_seconds: int = 60
    heartbeat_seconds: int = 15
    max_attempts: int = 3
    poll_seconds: float = 2.0
    queue_limit: int = 500
    python_time_multiplier: float = 2.0


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build settings from environment variables, falling back to the defaults above."""
    env = os.environ if environ is None else environ
    defaults = Settings()
    return Settings(
        database_url=env.get("DATABASE_URL", defaults.database_url),
        redis_url=env.get("REDIS_URL", defaults.redis_url),
        log_level=env.get("LOG_LEVEL", defaults.log_level).upper(),
        problems_dir=Path(env.get("PROBLEMS_DIR", str(defaults.problems_dir))),
        session_cookie_secure=_bool(env, "SESSION_COOKIE_SECURE", defaults.session_cookie_secure),
        lease_seconds=_positive_int(env, "LEASE_SECONDS", defaults.lease_seconds),
        heartbeat_seconds=_positive_int(env, "HEARTBEAT_SECONDS", defaults.heartbeat_seconds),
        max_attempts=_positive_int(env, "MAX_ATTEMPTS", defaults.max_attempts),
        poll_seconds=_positive_float(env, "POLL_SECONDS", defaults.poll_seconds),
        queue_limit=_positive_int(env, "QUEUE_LIMIT", defaults.queue_limit),
        python_time_multiplier=_positive_float(
            env, "PYTHON_TIME_MULTIPLIER", defaults.python_time_multiplier
        ),
    )


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value
