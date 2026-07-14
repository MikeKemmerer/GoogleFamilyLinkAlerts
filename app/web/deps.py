"""Shared FastAPI dependencies for the web UI."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from ..config import settings
from ..db.models import LatestSnapshot
from ..db.session import get_engine

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def get_db() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def build_auth_client():
    from ..familylink.auth_client import AuthClient
    return AuthClient(base_url=settings.familylink_auth_base_url, api_key=settings.familylink_auth_api_key)


def to_local(dt: datetime) -> datetime:
    """Convert a stored UTC timestamp (naive or aware -- see
    app/db/models.py:_utcnow) to the family's configured local timezone, so
    the web UI never shows a bare timestamp a parent has to mentally convert
    from UTC.
    """
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(settings.zone_info)


def last_poll_times(session: Session) -> dict[str, datetime]:
    """Per-child "last successful poll" time, localized.

    `LatestSnapshot.updated_at` is bumped every time a child's snapshot is
    successfully fetched and stored (see app/poller.py:poll_once) -- both on
    a brand new baseline poll and on every steady-state poll -- so it's an
    accurate proxy for "when did we last successfully hear from Family Link
    for this child", independent of whether *other* children in the same
    cycle failed (each child's fetch is independent; see poll_once's
    per-child try/except).
    """
    snapshots = session.exec(select(LatestSnapshot)).all()
    return {s.child_id: to_local(s.updated_at) for s in snapshots}
