import argparse
import getpass
from collections.abc import Sequence

from verdict.core.accounts import AccountError, create_user
from verdict.core.config import Settings, load_settings
from verdict.core.db import create_db_engine, create_session_factory
from verdict.core.logs import configure_logging


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m verdict.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create_admin_parser = commands.add_parser("create-admin", help="create an administrator")
    create_admin_parser.add_argument("username")
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)
    if args.command == "create-admin":
        create_admin(settings, args.username)


def create_admin(settings: Settings, username: str) -> None:
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("The two passwords don't match.")
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            create_user(db, username, password, is_admin=True)
    except AccountError as error:
        raise SystemExit(str(error)) from None
    finally:
        engine.dispose()
    print(f"Created administrator {username}.")


if __name__ == "__main__":
    main()
