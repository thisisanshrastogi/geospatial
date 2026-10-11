"""SQLAlchemy engine + session factory.

One engine (with its built-in connection pool) is shared by requests and
background jobs. SQLite needs `check_same_thread=False` plus WAL mode so a
request thread and a background thread can coexist.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

# Global engine and session factory - initialized by init_engine()
engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def init_engine(settings: Settings) -> None:
    """Build the global engine and session factory from `settings`."""
    global engine, SessionLocal

    database_url = settings.database_url
    connect_args = {}

    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

        @event.listens_for(Engine, "connect")
        def set_sqlite_pragma(dbapi_connection: object, connection_record: object) -> None:  # noqa: ARG001
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Session:
    """Get a fresh Session for one unit of work."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_engine() first.")
    return SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for database sessions."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_engine() first.")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()