"""Typed accessors over the AppSetting key/value table.

Centralizes the setting keys/defaults so the poller and the web UI
(setup wizard + settings page) agree on the same names and defaults.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, select

from .models import AppSetting
from ..config import settings as app_settings
from ..notify.categories import DEFAULT_ENABLED_CATEGORIES

DEFAULT_POLL_INTERVAL_MINUTES = 20

_KEY_POLL_INTERVAL = "poll_interval_minutes"
_KEY_NTFY_SERVER = "ntfy_server_url"
_KEY_NTFY_TOPIC = "ntfy_topic"
_KEY_SETUP_COMPLETED = "setup_completed"
_KEY_NOTIFICATIONS_ENABLED = "notifications_enabled"
_KEY_ENABLED_NOTIFICATION_CATEGORIES = "enabled_notification_categories"
_KEY_TIMEZONE = "timezone"


def get(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(AppSetting, key)
    return row.value if row else default


def set_(session: Session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row:
        row.value = value
        session.add(row)
    else:
        session.add(AppSetting(key=key, value=value))
    session.commit()


def get_poll_interval_minutes(session: Session) -> int:
    raw = get(session, _KEY_POLL_INTERVAL)
    try:
        return int(raw) if raw else DEFAULT_POLL_INTERVAL_MINUTES
    except ValueError:
        return DEFAULT_POLL_INTERVAL_MINUTES


def set_poll_interval_minutes(session: Session, minutes: int) -> None:
    set_(session, _KEY_POLL_INTERVAL, str(minutes))


def get_ntfy_config(session: Session) -> tuple[str, str] | None:
    server = get(session, _KEY_NTFY_SERVER)
    topic = get(session, _KEY_NTFY_TOPIC)
    if server and topic:
        return server, topic
    return None


def set_ntfy_config(session: Session, server_url: str, topic: str) -> None:
    set_(session, _KEY_NTFY_SERVER, server_url)
    set_(session, _KEY_NTFY_TOPIC, topic)


def is_setup_completed(session: Session) -> bool:
    return get(session, _KEY_SETUP_COMPLETED) == "true"


def mark_setup_completed(session: Session) -> None:
    set_(session, _KEY_SETUP_COMPLETED, "true")


def get_notifications_enabled(session: Session) -> bool:
    """Whether ntfy alerts should actually be sent (default: on).

    This gates delivery only -- change/failure records are still persisted
    and visible on the History page either way, so muting notifications
    never hides data, just the push alerts.
    """
    raw = get(session, _KEY_NOTIFICATIONS_ENABLED)
    return raw != "false"


def set_notifications_enabled(session: Session, enabled: bool) -> None:
    set_(session, _KEY_NOTIFICATIONS_ENABLED, "true" if enabled else "false")


def get_enabled_notification_categories(session: Session) -> set[str]:
    """Which notification categories (see app/notify/categories.py) should
    actually be pushed to ntfy. Absent setting (never saved yet) means
    "all enabled", preserving behavior from before this feature existed --
    an explicitly saved empty set means "none", which is different from
    "not yet configured".
    """
    raw = get(session, _KEY_ENABLED_NOTIFICATION_CATEGORIES)
    if raw is None:
        return set(DEFAULT_ENABLED_CATEGORIES)
    if raw == "":
        return set()
    return set(raw.split(","))


def set_enabled_notification_categories(session: Session, categories: set[str]) -> None:
    set_(session, _KEY_ENABLED_NOTIFICATION_CATEGORIES, ",".join(sorted(categories)))


def is_valid_timezone(tz: str) -> bool:
    try:
        ZoneInfo(tz)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def get_timezone(session: Session) -> str:
    """The IANA timezone name (e.g. "America/New_York") used to render
    dates/times in this app's web UI and to evaluate Family Link's
    bedtime/school-time schedules (which are configured by local
    time-of-day). Display/interpretation only -- never sent to or changed
    in the actual Google Family Link account.

    Falls back to the `TIMEZONE` env var (`app.config.settings.timezone`,
    set once at container start) until a value is explicitly saved here via
    the Settings page, so upgrading doesn't silently change existing
    behavior for anyone who already configured TIMEZONE in `.env`.
    """
    return get(session, _KEY_TIMEZONE) or app_settings.timezone


def set_timezone(session: Session, tz: str) -> None:
    set_(session, _KEY_TIMEZONE, tz)


def get_zone_info(session: Session) -> ZoneInfo:
    tz = get_timezone(session)
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def all_enabled_children(session: Session):
    from .models import Child
    return session.exec(select(Child).where(Child.enabled == True)).all()  # noqa: E712
