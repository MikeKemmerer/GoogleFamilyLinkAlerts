"""Shared FastAPI dependencies for the web UI."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from ..config import settings
from ..db import settings_store
from ..db.models import LatestSnapshot
from ..db.session import get_engine

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def get_db() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def build_auth_client():
    from ..familylink.auth_client import AuthClient
    return AuthClient(base_url=settings.familylink_auth_base_url, api_key=settings.familylink_auth_api_key)


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    """Convert a stored UTC timestamp (naive or aware -- see
    app/db/models.py:_utcnow) to `tz` (the family's configured *display*
    timezone -- see app/db/settings_store.py:get_zone_info) so the web UI
    never shows a bare timestamp a parent has to mentally convert from UTC.
    """
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(tz)


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
    tz = settings_store.get_zone_info(session)
    snapshots = session.exec(select(LatestSnapshot)).all()
    return {s.child_id: to_local(s.updated_at, tz) for s in snapshots}


def render(request, template_name: str, session: Session, context: dict):
    """`templates.TemplateResponse` wrapper that always injects context every
    page needs regardless of which route renders it -- currently just the
    saved display theme (see settings_store.get_theme). Every route that
    extends base.html should go through this rather than calling
    `templates.TemplateResponse` directly, so a shared context value like
    theme can never be silently missing from one page but not another
    (which is exactly how the Settings page's theme picker used to have no
    effect anywhere except the Settings page itself).
    """
    merged = {"theme": settings_store.get_theme(session), **context}
    return templates.TemplateResponse(request, template_name, merged)
