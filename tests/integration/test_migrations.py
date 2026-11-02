from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect

from tests.integration.conftest import recreate_database, run_alembic
from verdict.core.config import load_settings
from verdict.core.db import Base, create_db_engine

MIGRATIONS_DATABASE = "verdict_migrations_test"


def table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_migrations_upgrade_and_downgrade_cleanly_on_an_empty_database() -> None:
    url = recreate_database(load_settings().database_url, MIGRATIONS_DATABASE)
    engine = create_db_engine(url)
    every_table = {*Base.metadata.tables, "alembic_version"}
    try:
        run_alembic(engine, command.upgrade, "head")
        assert table_names(engine) == every_table
        run_alembic(engine, command.downgrade, "base")
        assert table_names(engine) == {"alembic_version"}
        run_alembic(engine, command.upgrade, "head")
        assert table_names(engine) == every_table
    finally:
        engine.dispose()


def test_migrated_schema_matches_the_models(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []
