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
from uuid import uuid4

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Child(SQLModel, table=True):
    """A supervised child discovered from Family Link."""

    id: str = Field(primary_key=True, description="Google account/user ID of the child")
    name: str
    avatar_url: str | None = Field(default=None, description="Google profile photo URL (from families/mine/members)")
    enabled: bool = Field(default=True, description="Whether polling is active for this child")
    auto_revoke_bonus_time: bool = Field(
        default=False,
        description="If true, the poller automatically cancels any active time-bonus override for this child's devices on the next poll (see app/poller.py's _enforce_auto_revoke_bonus_time)",
    )
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
    icon_url: str | None = Field(default=None, description="App icon URL, from apps_and_usage.apps[].iconUrl")
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


# Fixed, coarse account roles -- see app/web/auth.py and app/web/deps.py for
# the actual permission checks. Ordered weakest-to-strongest; a numeric
# ranking of these lives alongside require_role() in app/web/deps.py.
ROLE_VIEWER = "viewer"
ROLE_CONTRIBUTOR = "contributor"
ROLE_ADMIN = "admin"
VALID_ROLES = (ROLE_VIEWER, ROLE_CONTRIBUTOR, ROLE_ADMIN)


class User(SQLModel, table=True):
    """A logged-in account for this app's own web UI (unrelated to the
    Google account(s) being monitored).

    Only created/used once an admin turns on the optional `auth_enabled`
    setting (see app/db/settings_store.py) -- this table can be completely
    empty on an install that never enables auth, in which case every page
    behaves exactly as before this feature existed (see
    app/web/deps.py:require_role).

    `timezone`/`theme` are per-user overrides of the app-wide display
    timezone/theme (app/db/settings_store.py:get_timezone/get_theme) -- NULL
    means "use the global default", so a user who's never touched their own
    preference isn't stuck on some arbitrary value.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Random UUID, not a Google account ID")
    username: str = Field(unique=True, index=True)
    password_hash: str
    role: str = Field(default=ROLE_VIEWER, description="One of VALID_ROLES")
    timezone: str | None = Field(default=None, description="Per-user display timezone override; falls back to the global setting when unset")
    theme: str | None = Field(default=None, description="Per-user theme override ('auto'/'light'/'dark'); falls back to the global setting when unset")
    created_at: datetime = Field(default_factory=_utcnow)


class AppUsageHourlyBucket(SQLModel, table=True):
    """Incremental per-app usage seconds, bucketed by the hour they were
    *observed* during a poll (not necessarily the hour the usage actually
    happened -- Family Link's `appUsageSessions` only reports a running
    per-day total per app, with no per-session start/end timestamps at all,
    so true minute-level attribution isn't possible from the API).

    Built by app/poller.py: each poll cycle diffs the new per-app-per-day
    total against the previous poll's total for that same (child, app,
    date); the positive delta is added to the bucket for the current hour
    (in the display timezone) of *this* poll. Resolution is therefore
    limited to the poll interval, and only accumulates going forward from
    when this feature shipped -- there is no way to backfill history that
    predates it. Used to render the "Usage over the day" stacked area chart
    on the Status page (app/web/status.py).
    """

    child_id: str = Field(primary_key=True, foreign_key="child.id")
    package_name: str = Field(primary_key=True)
    local_date: str = Field(primary_key=True, description="ISO date (YYYY-MM-DD) in the display timezone, matching appUsageSessions[].date")
    hour: int = Field(primary_key=True, description="0-23, the display-timezone hour this poll ran in")
    seconds: float = Field(default=0.0)
    updated_at: datetime = Field(default_factory=_utcnow)


class GuestPermission(SQLModel, table=True):
    """A single granular on/off toggle controlling what the (optional)
    no-password "Continue as guest" session is allowed to see.

    Deliberately a flexible key/value table (one row per toggle) rather than
    fixed columns, so new guest-visible categories can be added later as
    plain rows/migrations-free inserts instead of a schema migration each
    time -- mirrors the AppSetting key/value pattern above. Only an admin
    can edit these (see app/web/settings.py); everything defaults to
    disabled (row absent == False) when guest mode is first turned on, so a
    guest sees nothing until an admin deliberately opts categories in.

    `category` values in use (see app/web/guest_permissions.py for the
    canonical list + labels):
      - "page:status", "page:history" -- whether the whole page is visible
      - "child:<child_id>" -- whether a specific child's data is visible
      - "data:screen_time", "data:bonus_time", "data:app_blocking",
        "data:bedtime_schooltime", "data:location", "data:battery"
      - "history:show_actor", "history:full_pagination"
    """

    category: str = Field(primary_key=True)
    enabled: bool = Field(default=False)
