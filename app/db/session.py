"""Database engine/session helpers."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from ..config import settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings.app_data_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    return _engine


def init_db() -> None:
    """Create tables if they don't exist yet.

    Normal schema evolution should go through Alembic migrations (see
    app/db/migrations/) -- this is only a safety net for a brand new,
    empty database on first run.
    """
    SQLModel.metadata.create_all(get_engine())


@contextmanager
def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
