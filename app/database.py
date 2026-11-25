import os
from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///grade_moderation.db")


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if dbapi_connection.__class__.__module__.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db(database_url: str | None = None) -> tuple[Engine, sessionmaker]:
    """Create all tables and append-only audit triggers.

    This function is repeatable and leaves existing data unchanged.
    """
    url = database_url or DATABASE_URL
    local_engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )

    @event.listens_for(local_engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        if dbapi_connection.__class__.__module__.startswith("sqlite"):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    Base.metadata.create_all(local_engine)
    if local_engine.dialect.name == "sqlite":
        with local_engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                BEFORE UPDATE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit_events is append-only');
                END;
            """)
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                BEFORE DELETE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit_events is append-only');
                END;
            """)

    local_session = sessionmaker(bind=local_engine, expire_on_commit=False, class_=Session)
    return local_engine, local_session


@contextmanager
def get_session(database_url: str | None = None) -> Iterator[Session]:
    _, local_session = init_db(database_url)
    session = local_session()
    try:
        yield session
    finally:
        session.close()
