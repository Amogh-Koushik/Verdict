import getpass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.forms import log_in, register
from verdict.cli import main
from verdict.core.accounts import find_user
from verdict.core.config import Settings

PASSWORD = "admin password 1"


@pytest.fixture
def answer_prompts(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> list[str]:
    """Point the command at the test database and feed its password prompts from a list."""
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    answers: list[str] = []
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": answers.pop(0))
    return answers


def test_create_admin_makes_an_administrator_who_can_log_in(
    answer_prompts: list[str],
    client: TestClient,
    db: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answer_prompts.extend([PASSWORD, PASSWORD])
    main(["create-admin", "root_admin"])
    assert "Created administrator root_admin." in capsys.readouterr().out
    admin = find_user(db, "root_admin")
    assert admin is not None
    assert admin.is_admin is True
    assert log_in(client, "root_admin", PASSWORD).status_code == 303


def test_create_admin_refuses_a_username_taken_in_another_case(
    answer_prompts: list[str], client: TestClient
) -> None:
    register(client, "alice", "correct horse battery")
    answer_prompts.extend([PASSWORD, PASSWORD])
    with pytest.raises(SystemExit, match="already taken"):
        main(["create-admin", "ALICE"])


def test_create_admin_refuses_mismatched_passwords(answer_prompts: list[str], db: Session) -> None:
    answer_prompts.extend([PASSWORD, PASSWORD + "!"])
    with pytest.raises(SystemExit, match="don't match"):
        main(["create-admin", "root_admin"])
    assert find_user(db, "root_admin") is None
