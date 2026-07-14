"""SQLModel data model.

Design: rather than a rigid per-setting-type schema, we store the latest
raw snapshot per child as JSON and let the diff engine (app/diff/engine.py)
flatten it into field paths for comparison. This keeps the model agnostic to
which Family Link data sources exist today vs. get added later (e.g. once
website_filter.py is implemented) -- no migration needed to support a new
field, only to support a new *table*.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Child(SQLModel, table=True):
    """A supervised child discovered from Family Link."""

    id: str = Field(primary_key=True, description="Google account/user ID of the child")
    name: str
    enabled: bool = Field(default=True, description="Whether polling is active for this child")
    created_at: datetime = Field(default_factory=_utcnow)


class LatestSnapshot(SQLModel, table=True):
    """The most recent raw settings snapshot captured for a child.

    One row per child; overwritten on every successful poll. `data` holds
    the merged dict produced by the poller (screen time, app limits,
    bedtime/school time, device state, and -- once implemented -- website
    filters), keyed by source name.
    """

    child_id: str = Field(primary_key=True, foreign_key="child.id")
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=_utcnow)


class ChangeEvent(SQLModel, table=True):
    """A single detected difference between two consecutive snapshots."""

    id: int | None = Field(default=None, primary_key=True)
    child_id: str = Field(foreign_key="child.id", index=True)
    field_path: str = Field(index=True, description="Dot/bracket path into the snapshot, e.g. apps[com.tiktok].blocked")
    old_value: Any = Field(default=None, sa_column=Column(JSON))
    new_value: Any = Field(default=None, sa_column=Column(JSON))
    detected_at: datetime = Field(default_factory=_utcnow, index=True)
    notified: bool = Field(default=False, description="Whether a ntfy alert was sent for this event")


class PollFailure(SQLModel, table=True):
    """Records auth/network failures so the web UI and ntfy can surface them.

    Distinct from ChangeEvent because these represent *our ability to poll*
    failing, not a change in the child's settings.
    """

    id: int | None = Field(default=None, primary_key=True)
    occurred_at: datetime = Field(default_factory=_utcnow, index=True)
    kind: str = Field(description="e.g. 'auth_required', 'session_expired', 'network_error'")
    message: str
    notified: bool = Field(default=False)


class AppRule(SQLModel, table=True):
    """A per-child app that has been seen blocked at least once.

    Rows are auto-discovered by the poller (see app/poller.py) whenever an
    app's `supervisionSetting.hidden` flag is observed True for a child --
    this just builds up a running list for the Settings page, regardless of
    `always_blocked`. Setting `always_blocked=True` opts a specific app into
    enforcement: if a later poll finds it re-enabled, the poller immediately
    re-blocks it via FamilyLinkApiClient.block_app (see NOTICE.md -- this is
    the one write/mutation capability this project supports, and only for
    apps a parent explicitly opted in here).
    """

    child_id: str = Field(primary_key=True, foreign_key="child.id")
    package_name: str = Field(primary_key=True)
    title: str
    always_blocked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AppSetting(SQLModel, table=True):
    """Simple key/value store for first-run wizard + ongoing app settings.

    Populated by the setup wizard; editable later from the Settings page.
    Not used for per-child data -- see LatestSnapshot for that.
    """

    key: str = Field(primary_key=True)
    value: str
