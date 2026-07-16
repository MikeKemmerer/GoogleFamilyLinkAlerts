"""Notification categories -- lets a parent choose which *kinds* of
detected changes actually get pushed to ntfy.

This only gates the push alert: every change is still recorded and shown
on the History page regardless of category settings (see
app/web/history.py). See app/poller.py for where the filtering is applied
and app/web/settings.py / app/templates/settings.html for the checkbox UI.
"""
from __future__ import annotations

import re

# Display order here also drives the order the checkboxes render in on the
# Settings page. Keys are persisted in AppSetting (see
# app/db/settings_store.py) -- don't rename an existing key without a
# migration path, since that would silently reset a parent's choice back
# to "all enabled".
CATEGORIES: dict[str, str] = {
    "app_blocking": "App blocked / unblocked / auto re-blocked",
    "screen_time": "Screen time & limits (daily limit, used/remaining)",
    "bonus_time": "Bonus/extra time (granted, revoked)",
    "bedtime_schooltime": "Bedtime & school time schedule",
    "device_lock": "Device lock state",
    "polling_issues": "Polling issues (login/network failures)",
    "other": "Everything else",
}

# Default: everything enabled, matching behavior before this feature existed.
DEFAULT_ENABLED_CATEGORIES: frozenset[str] = frozenset(CATEGORIES)

_CATEGORY_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"^apps_and_usage\.apps\[[^\]]+\]\.supervisionSetting\.hidden$"), "app_blocking"),
    (re.compile(
        r"^applied_time_limits\.devices\.[^.]+\."
        r"(total_allowed_minutes|used_minutes|remaining_minutes|"
        r"daily_limit_enabled|daily_limit_minutes)$"
    ), "screen_time"),
    (re.compile(
        r"^applied_time_limits\.devices\.[^.]+\."
        r"(bonus_minutes|bonus_override_id)$"
    ), "bonus_time"),
    (re.compile(r"^applied_time_limits\.(bedtime_enabled_today|schooltime_enabled_today)$"), "bedtime_schooltime"),
    (re.compile(
        r"^applied_time_limits\.devices\.[^.]+\."
        r"(bedtime_window\.(start_ms|end_ms)|schooltime_window\.(start_ms|end_ms)|"
        r"bedtime_active|schooltime_active)$"
    ), "bedtime_schooltime"),
    (re.compile(r"^applied_time_limits\.device_lock_states\.[^.]+$"), "device_lock"),
)


def category_for_field_path(field_path: str) -> str:
    """Best-effort category for a ChangeEvent's field_path.

    Anything that doesn't match a known pattern (e.g. a future
    website_filter.* field once that data source is implemented) falls
    back to "other" rather than being silently dropped from notification
    filtering altogether.
    """
    for pattern, category in _CATEGORY_PATTERNS:
        if pattern.match(field_path):
            return category
    return "other"
