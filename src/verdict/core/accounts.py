import re
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from psycopg.errors import UniqueViolation
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from verdict.core.models import User

USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,32}")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

# argon2-cffi's defaults are Argon2id with the parameters its maintainers currently recommend.
_hasher = PasswordHasher()


class AccountError(ValueError):
    """A registration request that can't be accepted; the message is shown to the user."""


class InvalidUsernameError(AccountError):
    pass


class InvalidPasswordError(AccountError):
    pass


class UsernameTakenError(AccountError):
    pass


def validate_username(username: str) -> None:
    if not USERNAME_PATTERN.fullmatch(username):
        raise InvalidUsernameError(
            "Usernames are 3 to 32 characters: letters, digits, underscores and hyphens."
        )


def validate_password(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise InvalidPasswordError(
            f"Passwords are {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters long."
        )


def create_user(db: Session, username: str, password: str, *, is_admin: bool = False) -> User:
    validate_username(username)
    validate_password(password)
    user = User(username=username, password_hash=_hasher.hash(password), is_admin=is_admin)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        # Relying on the unique index rather than a lookup first means two concurrent
        # registrations of the same name can't both succeed.
        if _is_username_conflict(error):
            raise UsernameTakenError(f"The username {username!r} is already taken.") from None
        raise
    return user


def find_user(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.username) == func.lower(username)))


def authenticate(db: Session, username: str, password: str) -> User | None:
    """Return the user if the password matches, or None for any wrong username or password."""
    user = find_user(db, username)
    if user is None:
        # Spend the same time as a real check so response times don't reveal which names exist.
        _password_matches(_unused_account_hash(), password)
        return None
    if not _password_matches(user.password_hash, password):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
        db.commit()
    return user


def _password_matches(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


@cache
def _unused_account_hash() -> str:
    return _hasher.hash("no account has this password")


def _is_username_conflict(error: IntegrityError) -> bool:
    return (
        isinstance(error.orig, UniqueViolation)
        and error.orig.diag.constraint_name == "uq_users_username_lower"
    )
