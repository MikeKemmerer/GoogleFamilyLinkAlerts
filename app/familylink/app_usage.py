"""Shared helpers for interpreting Family Link's ``appUsageSessions`` data.

Used by both app/poller.py (to accumulate the hourly usage buckets used by
the Status page's stacked-area chart) and app/web/status.py (to render the
existing per-day usage summary bar) so both stay consistent about how a raw
session's ``usage``/``date`` fields are parsed -- and so callers share one
definition of "which color represents this app" (`app_usage_color_var`).

Important limitation: `appUsageSessions[*]` only reports a running total of
seconds used *per app per calendar day* -- there are no per-session
start/end timestamps at all, so nothing in this app can determine which
hour of the day usage actually happened in. `hourly_usage_deltas` (used by
the poller) approximates this by attributing newly-observed usage to the
hour it was *detected* during a poll, not the hour it was actually used.
"""
from __future__ import annotations

from datetime import date

APP_USAGE_COLOR_VARS = [
    "--app-usage-color-1",
    "--app-usage-color-2",
    "--app-usage-color-3",
    "--app-usage-color-4",
    "--app-usage-color-5",
    "--app-usage-color-6",
    "--app-usage-color-7",
    "--app-usage-color-8",
]


def app_usage_color_var(rank: int) -> str:
    """Return the color variable for the app at position ``rank`` (0-based)
    within a usage-sorted list of apps for the same day/chart.

    Colors are assigned by *rank*, not by hashing the package name, so that
    neighboring apps in a usage-ordered visual (the summary bar's adjacent
    segments, or the stacked chart's adjacent bands) always get different,
    well-separated colors -- ``APP_USAGE_COLOR_VARS`` is ordered so that
    consecutive entries have maximally distinct hues. Hashing the package
    name instead could coincidentally assign the same (or a very similar)
    color to two apps that end up sitting right next to each other.
    """
    return APP_USAGE_COLOR_VARS[rank % len(APP_USAGE_COLOR_VARS)]


def parse_usage_seconds(value: object) -> float | None:
    """Parse Family Link's app-usage duration strings like ``"123.4s"``."""
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        seconds = float(value[:-1])
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def format_usage_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return f"{seconds_part}s"


def usage_totals_by_app_and_date(apps_and_usage: dict | None) -> dict[tuple[str, date], float]:
    """Sum ``appUsageSessions`` into ``{(package_name, local_date): seconds}``.

    A given (app, date) pair can appear across multiple sessions/devices in
    the raw payload, so these are summed rather than taking the last one.
    """
    totals: dict[tuple[str, date], float] = {}
    if not isinstance(apps_and_usage, dict):
        return totals

    for session in apps_and_usage.get("appUsageSessions") or []:
        if not isinstance(session, dict):
            continue
        session_date = session.get("date") or {}
        try:
            local_date = date(session_date["year"], session_date["month"], session_date["day"])
        except (KeyError, TypeError, ValueError):
            continue
        app_id = session.get("appId") or {}
        if not isinstance(app_id, dict):
            continue
        package_name = app_id.get("androidAppPackageName")
        if not isinstance(package_name, str) or not package_name:
            continue
        seconds = parse_usage_seconds(session.get("usage"))
        if seconds is None:
            continue
        key = (package_name, local_date)
        totals[key] = totals.get(key, 0.0) + seconds

    return totals


def hourly_usage_deltas(
    old_apps_and_usage: dict | None, new_apps_and_usage: dict | None
) -> dict[tuple[str, date], float]:
    """Compare two consecutive polls' per-(app, date) totals.

    Returns only positive deltas (new usage observed since the last poll),
    keyed the same way as `usage_totals_by_app_and_date`. Negative/zero
    deltas (e.g. a day rolling over, or Family Link revising a total down)
    are dropped rather than recorded as negative usage.
    """
    old_totals = usage_totals_by_app_and_date(old_apps_and_usage)
    new_totals = usage_totals_by_app_and_date(new_apps_and_usage)

    deltas: dict[tuple[str, date], float] = {}
    for key, new_seconds in new_totals.items():
        delta = new_seconds - old_totals.get(key, 0.0)
        if delta > 0:
            deltas[key] = delta
    return deltas
