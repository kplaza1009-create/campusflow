"""Synchronous SQLAlchemy engine and session factory.

Route handlers are plain `def` functions, so FastAPI runs them in its threadpool.
A blocking database call therefore ties up one worker thread, not the event loop.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


# Every session runs in UTC, so timestamps come back the same way whatever the server's
# default zone is (a local PostgreSQL may default to the machine's zone; Neon uses UTC).
# Needs a direct connection: some connection poolers drop startup options.
engine = create_engine(
    get_settings().database_url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"}
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """One database session per request; rolled back if the handler doesn't commit."""
    with SessionLocal() as db:
        yield db
