import os
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import config


def _make_engine():
    if config.IS_POSTGRES:
        # pool_pre_ping: hosted Postgres (Neon) closes idle connections, so check before use.
        return create_engine(config.DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
    if config.DATABASE_URL.startswith("sqlite:///./"):
        os.makedirs(os.path.dirname(config.DATABASE_URL.removeprefix("sqlite:///")) or ".", exist_ok=True)
    # check_same_thread=False: FastAPI hands one session to several threads over a request.
    return create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})


engine = _make_engine()

if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _sqlite_foreign_keys(dbapi_connection, _record):
        # SQLite ignores foreign keys (and ON DELETE CASCADE) unless each connection turns them on.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamp. Every datetime column stores naive UTC, the same in SQLite and Postgres."""
    return datetime.now(UTC).replace(tzinfo=None)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
