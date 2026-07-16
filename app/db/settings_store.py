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
_KEY_THEME = "theme"
_KEY_AUTH_ENABLED = "auth_enabled"
_KEY_GUEST_VIEW_ENABLED = "guest_view_enabled"
_KEY_SESSION_SECRET = "session_secret"

VALID_THEMES = ("auto", "light", "dark")
DEFAULT_THEME = "auto"


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


CURATED_TIMEZONES: list[tuple[str, list[str]]] = [
    ("US & Canada", [
        "America/New_York", "America/Chicago", "America/Denver", "America/Phoenix",
        "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu", "America/Toronto",
        "America/Vancouver",
    ]),
    ("Europe", [
        "Europe/London", "Europe/Dublin", "Europe/Paris", "Europe/Berlin", "Europe/Madrid",
        "Europe/Rome", "Europe/Amsterdam", "Europe/Zurich", "Europe/Athens", "Europe/Moscow",
    ]),
    ("Asia & Middle East", [
        "Asia/Tokyo", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Singapore", "Asia/Seoul",
        "Asia/Kolkata", "Asia/Dubai", "Asia/Bangkok", "Asia/Jakarta", "Asia/Istanbul",
    ]),
    ("Oceania", ["Australia/Sydney", "Australia/Melbourne", "Australia/Perth", "Pacific/Auckland"]),
    ("Africa & South America", [
        "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos", "America/Sao_Paulo",
        "America/Mexico_City", "America/Bogota", "America/Argentina/Buenos_Aires",
    ]),
    ("UTC", ["UTC"]),
]
"""A short, curated list of common IANA timezones (grouped by region) for
the Settings-page dropdown, rather than all ~600 zones in
zoneinfo.available_timezones() -- picked for the handful of zones a typical
family is actually in. See get_timezone_options() for how a currently-saved
value outside this list is still surfaced without silently disappearing."""


def get_timezone_options(session: Session) -> list[tuple[str, list[str]]]:
    """The curated timezone groups for the Settings dropdown, plus (if the
    currently-effective timezone isn't already one of the curated options)
    an extra "Current" group so a previously-saved or env-configured value
    is never silently dropped from the list a user sees.
    """
    current = get_timezone(session)
    curated_names = {name for _, names in CURATED_TIMEZONES for name in names}
    if current in curated_names:
        return CURATED_TIMEZONES
    return [("Current", [current])] + CURATED_TIMEZONES


def get_theme(session: Session) -> str:
    """UI color scheme preference: "auto" (follow the browser/OS
    prefers-color-scheme), "light", or "dark". Purely a display
    preference for this app's own web UI -- unrelated to Family Link.
    """
    value = get(session, _KEY_THEME)
    return value if value in VALID_THEMES else DEFAULT_THEME


def set_theme(session: Session, theme: str) -> None:
    if theme not in VALID_THEMES:
        raise ValueError(f"Invalid theme: {theme!r}")
    set_(session, _KEY_THEME, theme)


def all_enabled_children(session: Session):
    from .models import Child
    return session.exec(select(Child).where(Child.enabled == True)).all()  # noqa: E712


def get_auth_enabled(session: Session) -> bool:
    """Whether this app's own login system is turned on (default: off, so
    upgrading never locks out an existing install -- see app/web/auth.py
    and app/web/deps.py:require_role for what changes when this is True).
    """
    return get(session, _KEY_AUTH_ENABLED) == "true"


def set_auth_enabled(session: Session, enabled: bool) -> None:
    set_(session, _KEY_AUTH_ENABLED, "true" if enabled else "false")


def get_guest_view_enabled(session: Session) -> bool:
    """Whether the no-password "Continue as guest" login option is offered.

    Only has any effect when auth is also enabled. What a guest can
    actually see is controlled separately, per-category, via
    app/web/guest_permissions.py -- this flag only controls whether the
    guest login option exists at all.
    """
    return get(session, _KEY_GUEST_VIEW_ENABLED) == "true"


def set_guest_view_enabled(session: Session, enabled: bool) -> None:
    set_(session, _KEY_GUEST_VIEW_ENABLED, "true" if enabled else "false")


def get_or_create_session_secret(session: Session) -> str:
    """The signing key for this app's own login session cookie.

    Generated once (on first use) and persisted in the same SQLite database
    as everything else, rather than a separate file -- simplest option for
    a single-container app, and it naturally survives container
    recreation via the existing data/ volume mount. Never rotated
    automatically: rotating would immediately invalidate every existing
    login session.
    """
    import secrets
    existing = get(session, _KEY_SESSION_SECRET)
    if existing:
        return existing
    new_secret = secrets.token_hex(32)
    set_(session, _KEY_SESSION_SECRET, new_secret)
    return new_secret
