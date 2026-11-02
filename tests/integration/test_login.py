import time
from dataclasses import replace

from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine

from tests.integration.forms import log_in, log_out, register, session_token
from verdict.core.config import Settings
from verdict.web.main import create_app
from verdict.web.sessions import session_key

PASSWORD = "correct horse battery"
SEVEN_DAYS = 7 * 24 * 60 * 60


def registered(client: TestClient, username: str) -> None:
    register(client, username, PASSWORD)
    client.cookies.clear()


def is_logged_in_as(client: TestClient, username: str) -> bool:
    return f"You are logged in as <strong>{username}</strong>" in client.get("/").text


def wait_until_window_has_seconds_left(window_seconds: int, needed: float) -> None:
    # Fixed windows reset on the boundary; starting just before it would split the attempts.
    remaining = window_seconds - time.time() % window_seconds
    if remaining < needed:
        time.sleep(remaining)


def test_correct_password_logs_the_user_in(client: TestClient) -> None:
    registered(client, "alice")
    response = log_in(client, "alice", PASSWORD)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert is_logged_in_as(client, "alice")


def test_login_ignores_the_case_of_the_username(client: TestClient) -> None:
    registered(client, "Alice")
    assert log_in(client, "ALICE", PASSWORD).status_code == 303
    assert is_logged_in_as(client, "Alice")


def test_wrong_password_is_rejected(client: TestClient) -> None:
    registered(client, "alice")
    response = log_in(client, "alice", "not the password")
    assert response.status_code == 401
    assert "Wrong username or password." in response.text
    assert not is_logged_in_as(client, "alice")


def test_unknown_username_gets_the_same_message_as_a_wrong_password(client: TestClient) -> None:
    response = log_in(client, "nobody", PASSWORD)
    assert response.status_code == 401
    assert "Wrong username or password." in response.text


def test_login_form_escapes_the_submitted_username(client: TestClient) -> None:
    response = log_in(client, '"><script>alert(1)</script>', PASSWORD)
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_login_replaces_the_anonymous_session(client: TestClient, redis: Redis) -> None:
    registered(client, "alice")
    client.get("/login")
    anonymous_token = session_token(client)
    log_in(client, "alice", PASSWORD)
    user_token = session_token(client)
    assert anonymous_token is not None
    assert user_token is not None
    assert user_token != anonymous_token
    assert redis.exists(session_key(anonymous_token)) == 0
    assert redis.exists(session_key(user_token)) == 1


def test_logout_deletes_the_session(client: TestClient, redis: Redis) -> None:
    register(client, "alice", PASSWORD)
    token = session_token(client)
    assert token is not None
    response = log_out(client)
    assert response.status_code == 303
    assert redis.exists(session_key(token)) == 0
    assert session_token(client) is None
    assert not is_logged_in_as(client, "alice")


def test_session_cookie_is_http_only_same_site_lax_and_lasts_seven_days(
    client: TestClient, redis: Redis
) -> None:
    response = register(client, "alice", PASSWORD)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert f"Max-Age={SEVEN_DAYS}" in cookie
    assert "Secure" not in cookie
    token = session_token(client)
    assert token is not None
    assert SEVEN_DAYS - 5 < int(redis.ttl(session_key(token))) <= SEVEN_DAYS


def test_session_cookie_is_secure_when_configured(
    settings: Settings, engine: Engine, redis: Redis
) -> None:
    with TestClient(create_app(replace(settings, session_cookie_secure=True))) as client:
        assert "Secure" in client.get("/login").headers["set-cookie"]


def test_session_ends_when_its_lifetime_runs_out(
    settings: Settings, engine: Engine, redis: Redis
) -> None:
    with TestClient(create_app(replace(settings, session_ttl_seconds=2))) as client:
        register(client, "alice", PASSWORD)
        assert is_logged_in_as(client, "alice")
        time.sleep(2.5)
        assert not is_logged_in_as(client, "alice")


def test_sixth_login_attempt_within_a_minute_is_refused(client: TestClient) -> None:
    registered(client, "alice")
    wait_until_window_has_seconds_left(60, needed=10)
    for _ in range(5):
        assert log_in(client, "alice", "not the password").status_code == 401
    response = log_in(client, "alice", PASSWORD)
    assert response.status_code == 429
    assert "Too many login attempts" in response.text
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert not is_logged_in_as(client, "alice")


def test_login_limit_counts_each_username_separately(client: TestClient) -> None:
    registered(client, "alice")
    registered(client, "bob")
    wait_until_window_has_seconds_left(60, needed=10)
    for _ in range(5):
        log_in(client, "alice", "not the password")
    assert log_in(client, "bob", PASSWORD).status_code == 303
