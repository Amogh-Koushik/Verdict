from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, Engine, MetaData, create_engine, func, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeEngine

# Fixed constraint names keep migrations reproducible instead of relying on Postgres defaults.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, TypeEngine[Any]]] = {datetime: DateTime(timezone=True)}


def create_db_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # Objects stay readable after commit, so a route can commit and then render them.
    return sessionmaker(engine, expire_on_commit=False)


def ping_database(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def database_now(db: Session) -> datetime:
    """The database's clock; fixed for the transaction, so rows it inserts get this created_at."""
    return db.scalars(select(func.now())).one()
