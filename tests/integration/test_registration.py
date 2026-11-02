import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.integration.forms import register
from verdict.core.models import User

PASSWORD = "correct horse battery"


def count_users(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def test_registering_logs_the_new_user_in(client: TestClient) -> None:
    response = register(client, "alice", PASSWORD)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "You are logged in as <strong>alice</strong>" in client.get("/").text


def test_password_is_stored_only_as_an_argon2id_hash(client: TestClient, db: Session) -> None:
    register(client, "alice", PASSWORD)
    user = db.scalars(select(User)).one()
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    assert user.is_admin is False


def test_username_keeps_the_case_it_was_registered_with(client: TestClient, db: Session) -> None:
    register(client, "Alice", PASSWORD)
    assert db.scalars(select(User.username)).one() == "Alice"


def test_username_taken_in_another_case_is_rejected(client: TestClient, db: Session) -> None:
    register(client, "Alice", PASSWORD)
    client.cookies.clear()
    response = register(client, "aLiCe", PASSWORD)
    assert response.status_code == 409
    assert "already taken" in response.text
    assert count_users(db) == 1


@pytest.mark.parametrize("username", ["ab", "a" * 33, "has space", "dot.ted", "ünicode", ""])
def test_invalid_username_is_rejected(client: TestClient, db: Session, username: str) -> None:
    response = register(client, username, PASSWORD)
    assert response.status_code == 400
    assert "Usernames are 3 to 32 characters" in response.text
    assert count_users(db) == 0


@pytest.mark.parametrize("password", ["seven77", "x" * 129])
def test_password_outside_the_length_limits_is_rejected(
    client: TestClient, db: Session, password: str
) -> None:
    response = register(client, "alice", password)
    assert response.status_code == 400
    assert "Passwords are 8 to 128 characters long" in response.text
    assert count_users(db) == 0


def test_mismatched_password_confirmation_is_rejected(client: TestClient, db: Session) -> None:
    response = register(client, "alice", PASSWORD, confirm=PASSWORD + "!")
    assert response.status_code == 400
    assert "The two passwords don&#39;t match." in response.text
    assert count_users(db) == 0


def test_rejected_form_keeps_the_username_typed(client: TestClient) -> None:
    response = register(client, "alice", PASSWORD, confirm="something else")
    assert 'value="alice"' in response.text


def test_logged_in_user_is_sent_home_from_the_register_page(client: TestClient) -> None:
    register(client, "alice", PASSWORD)
    response = client.get("/register", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
