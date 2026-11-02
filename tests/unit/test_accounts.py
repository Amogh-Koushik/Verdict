import pytest

from verdict.core.accounts import (
    InvalidPasswordError,
    InvalidUsernameError,
    validate_password,
    validate_username,
)


@pytest.mark.parametrize("username", ["abc", "a" * 32, "Alice_2", "x-y-z", "007"])
def test_valid_usernames_are_accepted(username: str) -> None:
    validate_username(username)


@pytest.mark.parametrize(
    "username",
    ["", "ab", "a" * 33, "has space", " alice", "alice\n", "dot.ted", "ünicode", "<b>"],
)
def test_invalid_usernames_are_rejected(username: str) -> None:
    with pytest.raises(InvalidUsernameError):
        validate_username(username)


@pytest.mark.parametrize("password", ["x" * 8, "x" * 128, "pass phrase with spaces"])
def test_passwords_within_the_length_limits_are_accepted(password: str) -> None:
    validate_password(password)


@pytest.mark.parametrize("password", ["", "x" * 7, "x" * 129])
def test_passwords_outside_the_length_limits_are_rejected(password: str) -> None:
    with pytest.raises(InvalidPasswordError):
        validate_password(password)
