from alembic import context
from sqlalchemy import Connection

import verdict.core.models  # noqa: F401  (registers the tables on Base.metadata)
from verdict.core.config import load_settings
from verdict.core.db import Base, create_db_engine
from verdict.core.logs import configure_logging

settings = load_settings()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_on_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Tests pass their own connection and keep their own logging setup.
    connection = context.config.attributes.get("connection")
    if connection is not None:
        run_on_connection(connection)
        return
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    with engine.connect() as new_connection:
        run_on_connection(new_connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
