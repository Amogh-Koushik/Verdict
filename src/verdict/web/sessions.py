import secrets
from dataclasses import dataclass
from typing import cast

from fastapi import Response
from redis import Redis

from verdict.core.config import Settings

SESSION_COOKIE = "verdict_session"


@dataclass(frozen=True)
class BrowserSession:
    """A server-side session; anonymous until `user_id` is set at login."""

    token: str
    csrf_token: str
    user_id: int | None


def session_key(token: str) -> str:
    return f"session:{token}"


def create_session(redis: Redis, user_id: int | None, ttl_seconds: int) -> BrowserSession:
    session = BrowserSession(
        token=secrets.token_urlsafe(32),
        csrf_token=secrets.token_urlsafe(32),
        user_id=user_id,
    )
    key = session_key(session.token)
    # One MULTI/EXEC transaction, so a session can never exist without its expiry.
    with redis.pipeline() as pipe:
        pipe.hset(key, "csrf_token", session.csrf_token)
        if user_id is not None:
            pipe.hset(key, "user_id", str(user_id))
        pipe.expire(key, ttl_seconds)
        pipe.execute()
    return session


def load_session(redis: Redis, token: str) -> BrowserSession | None:
    fields = cast(dict[str, str], redis.hgetall(session_key(token)))
    if not fields:
        return None
    user_id = fields.get("user_id")
    return BrowserSession(
        token=token,
        csrf_token=fields["csrf_token"],
        user_id=int(user_id) if user_id else None,
    )


def delete_session(redis: Redis, token: str) -> None:
    redis.delete(session_key(token))


def set_session_cookie(response: Response, session: BrowserSession, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session.token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )
