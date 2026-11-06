"""Database engine, session factory, and declarative base (D4)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _apply_sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
    """WAL + FK enforcement + busy timeout (DATABASE_DESIGN conventions)."""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


settings.ensure_dirs()

engine: Engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite(settings.database_url) else {},
    future=True,
)
if _is_sqlite(settings.database_url):
    event.listens_for(engine, "connect")(_apply_sqlite_pragmas)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session; always closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
