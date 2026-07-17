"""Root status/dashboard page."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select
from zoneinfo import ZoneInfo

from ..config import settings
from ..db import settings_store
from ..db.models import AppUsageHourlyBucket, Child, LatestSnapshot
from ..diff.labels import app_titles_from_snapshot, device_names_from_snapshot, format_minutes
from ..familylink.app_usage import app_usage_color_var, format_usage_duration, usage_totals_by_app_and_date
from ..poller import poll_once
from . import guest_permissions
from .deps import (
    build_auth_client,
    get_db,
    get_effective_zone_info,
    last_poll_times,
    render,
    require_page_access,
    to_local,
)

router = APIRouter()



def _parse_location_timestamp(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _battery_badge_class(level: int | None) -> str:
    if level is None:
        return "badge"
    if level >= 60:
        return "badge-ok"
    if level >= 25:
        return "badge-warn"
    return "badge-bad"


def _latest_app_usage_date(apps_and_usage: dict[str, Any] | None) -> date | None:
    """Return the most recent calendar day present in ``appUsageSessions``.

    Each session's ``date`` is assigned by Family Link itself (not by us),
    using whatever day boundary/timezone it tracks for that child's device
    -- the same boundary it uses to compute the device's own ``used_minutes``
    total. Picking the freshest date present in the data (rather than
    "today" per our own configurable display timezone) keeps the per-app
    usage breakdown's day boundary aligned with that device total, even
    when the display timezone setting differs from the device's actual
    timezone -- avoiding a mismatch between "total time used today" and
    the sum of the per-app breakdown.
    """
    dates = {session_date for (_, session_date) in usage_totals_by_app_and_date(apps_and_usage)}
    return max(dates) if dates else None


def _build_app_usage_for_day(apps_and_usage: dict[str, Any] | None, target_date: date) -> list[dict[str, Any]]:
    """Aggregate ``appUsageSessions`` for one local calendar day."""
    if not isinstance(apps_and_usage, dict):
        return []

    app_titles = app_titles_from_snapshot({"apps_and_usage": apps_and_usage})
    totals: dict[str, float] = {}
    for (package_name, session_date), seconds in usage_totals_by_app_and_date(apps_and_usage).items():
        if session_date != target_date:
            continue
        totals[package_name] = totals.get(package_name, 0.0) + seconds

    if not totals:
        return []

    total_seconds = sum(totals.values())
    usage = [
        {
            "package_name": package_name,
            "app_title": app_titles.get(package_name, package_name),
            "seconds": seconds,
            "duration_display": format_usage_duration(seconds),
            "width_pct": (seconds / total_seconds) * 100,
            "color_var": app_usage_color_var(package_name),
        }
        for package_name, seconds in totals.items()
    ]
    usage.sort(key=lambda item: (-item["seconds"], item["app_title"].casefold(), item["package_name"]))
    return usage


def _build_hourly_app_usage_chart(
    session: Session, child_id: str, target_date: date, app_titles: dict[str, str], current_hour: int
) -> dict[str, Any] | None:
    """Build a stacked-area "usage over the day" chart from the hourly
    buckets the poller accumulates (see AppUsageHourlyBucket). Returns None
    if there's nothing recorded yet for this child/day -- e.g. the feature
    was only just enabled, so no deltas have been observed yet.

    ``target_date`` should be the same device-anchored day used for the
    per-app usage breakdown (see ``_latest_app_usage_date``) -- not the
    admin's configured display timezone's "today" -- so this chart's total
    stays consistent with that breakdown instead of silently reflecting a
    different calendar day whenever the device's own timezone differs from
    the display timezone.

    Only elapsed hours (0..current_hour, inclusive) are plotted -- the
    chart's x-axis ends at "now" rather than projecting a flat line across
    hours of the day that haven't happened yet.
    """
    rows = session.exec(
        select(AppUsageHourlyBucket).where(
            AppUsageHourlyBucket.child_id == child_id,
            AppUsageHourlyBucket.local_date == target_date.isoformat(),
        )
    ).all()
    if not rows:
        return None

    seconds_by_app_hour: dict[str, dict[int, float]] = {}
    totals_by_app: dict[str, float] = {}
    for row in rows:
        seconds_by_app_hour.setdefault(row.package_name, {})[row.hour] = row.seconds
        totals_by_app[row.package_name] = totals_by_app.get(row.package_name, 0.0) + row.seconds

    if not totals_by_app:
        return None

    # Order apps by total usage (most-used first) so the stack order/legend
    # matches the existing summary bar's ordering convention.
    ordered_packages = sorted(
        totals_by_app,
        key=lambda pkg: (-totals_by_app[pkg], app_titles.get(pkg, pkg).casefold(), pkg),
    )

    max_hour = max(0, min(current_hour, 23))
    domain_hours = max_hour + 1  # x-axis spans [0, domain_hours) -- i.e. through "now"

    series = []
    for package_name in ordered_packages:
        hourly = seconds_by_app_hour.get(package_name, {})
        cumulative_minutes = []
        running_total = 0.0
        for hour in range(domain_hours):
            running_total += hourly.get(hour, 0.0)
            cumulative_minutes.append(running_total / 60.0)
        series.append(
            {
                "package_name": package_name,
                "app_title": app_titles.get(package_name, package_name),
                "color_var": app_usage_color_var(package_name),
                "cumulative_minutes": cumulative_minutes,
                "total_display": format_usage_duration(totals_by_app[package_name]),
            }
        )

    # The chart's true visual maximum is the top of the topmost (stacked)
    # band -- i.e. the *sum* of every app's cumulative usage at whichever
    # hour the stack is tallest (always the last elapsed hour, since
    # cumulative totals only ever increase) -- not any single app's own
    # highest total. Using a per-app max here would under-scale the y-axis
    # whenever more than one app has usage, causing the stacked area to
    # visually overflow past the axis's stated max.
    max_cumulative = max(
        (sum(layer["cumulative_minutes"][hour] for layer in series) for hour in range(domain_hours)),
        default=0.0,
    )

    if max_cumulative <= 0:
        return None

    # Build an SVG stacked-area chart by hand (no charting library, matching
    # the project's no-CDN/no-JS-framework convention) -- one <path> per
    # app, using straight line segments between each hour's cumulative
    # value (smoothed rather than a step function -- a step shape reads as
    # more "precise" than the data actually is, since usage is only ever
    # observed at hourly resolution anyway). The last point is extended
    # flat to the right edge of the plotted domain so the area fills all
    # the way to "now" instead of stopping short at the last plotted hour.
    chart_width, chart_height = 720, 220

    def x_for_hour(hour: float) -> float:
        return (hour / domain_hours) * chart_width

    def y_for_minutes(minutes: float) -> float:
        return chart_height - (minutes / max_cumulative) * chart_height

    def line_points(values: list[float]) -> list[tuple[float, float]]:
        points = [(hour, values[hour]) for hour in range(domain_hours)]
        points.append((domain_hours, values[-1]))
        return points

    running_top = [0.0] * domain_hours
    for layer in series:
        bottom = list(running_top)
        top = [bottom[h] + layer["cumulative_minutes"][h] for h in range(domain_hours)]
        top_line = line_points(top)
        bottom_line = line_points(bottom)
        top_svg = " ".join(f"{x_for_hour(x):.1f},{y_for_minutes(y):.1f}" for x, y in top_line)
        bottom_svg = " ".join(f"{x_for_hour(x):.1f},{y_for_minutes(y):.1f}" for x, y in reversed(bottom_line))
        layer["path"] = f"M {top_svg} L {bottom_svg} Z"
        running_top = top

    hour_labels = [h for h in (0, 6, 12, 18) if h < domain_hours]
    if not hour_labels or hour_labels[-1] != max_hour:
        hour_labels.append(max_hour)
    hour_labels = [f"{h:02d}:00" if h != max_hour else "now" for h in hour_labels]

    # Y-axis ticks (0, 25%, 50%, 75%, 100% of max_cumulative), top-to-bottom
    # so the highest value lines up with the top of the chart. Labels live
    # in a separate HTML flex column next to the SVG (laid out with
    # `justify-content: space-between` so the top/bottom ticks always stay
    # fully inside their container) rather than as SVG <text> -- the chart
    # uses preserveAspectRatio="none", which would stretch/skew any text
    # placed directly inside it.
    tick_count = 5
    y_ticks = []
    for i in range(tick_count):
        frac = i / (tick_count - 1)
        value_minutes = max_cumulative * (1 - frac)
        y_ticks.append({
            "y": frac * chart_height,
            "label": format_usage_duration(value_minutes * 60),
        })

    return {
        "series": series,
        "chart_width": chart_width,
        "chart_height": chart_height,
        "hour_labels": hour_labels,
        "y_ticks": y_ticks,
        "max_display": format_usage_duration(max_cumulative * 60),
    }


def _build_location_context(raw_location: dict | None, tz: ZoneInfo) -> dict | None:
    if not isinstance(raw_location, dict):
        return None
    try:
        latitude = float(raw_location["latitude"])
        longitude = float(raw_location["longitude"])
    except (KeyError, TypeError, ValueError):
        return None

    accuracy = raw_location.get("accuracy")
    try:
        accuracy = int(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        accuracy = None

    battery_level = raw_location.get("battery_level")
    try:
        battery_level = int(battery_level) if battery_level is not None else None
    except (TypeError, ValueError):
        battery_level = None

    parsed_timestamp = _parse_location_timestamp(raw_location.get("timestamp"))
    updated_display = (
        to_local(parsed_timestamp, tz).strftime("%Y-%m-%d %H:%M")
        if parsed_timestamp
        else (raw_location.get("timestamp") or "unknown")
    )

    source_device_name = raw_location.get("source_device_name")
    if not isinstance(source_device_name, str) or not source_device_name.strip():
        source_device_name = None
    else:
        source_device_name = source_device_name.strip()

    place_name = raw_location.get("place_name")
    if not isinstance(place_name, str) or not place_name.strip():
        place_name = None
    else:
        place_name = place_name.strip()

    place_address = raw_location.get("place_address")
    if not isinstance(place_address, str) or not place_address.strip():
        place_address = None
    else:
        place_address = place_address.strip()

    return {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": accuracy,
        "timestamp": raw_location.get("timestamp"),
        "updated_display": updated_display,
        "place_name": place_name,
        "place_address": place_address,
        "source_device_name": source_device_name,
        "battery_level": battery_level,
        "battery_badge_class": _battery_badge_class(battery_level),
    }


def _build_device_summaries(
    session: Session,
    children: list[Child],
    guest_perms: dict[str, bool] | None,
    tz: ZoneInfo,
) -> list[dict]:
    """Per-child device activity summary for the Status page: total minutes
    used today (summed across all their devices) plus per-device screen
    time, lock/bedtime badges, and (when available/permitted) last-known
    location + battery data already stored in LatestSnapshot.

    `guest_perms` is None for a real logged-in user/no-auth (show
    everything); for a guest session it's their admin-configured
    permissions dict, used to omit bonus time / bedtime+schooltime /
    location / battery detail they haven't been explicitly granted (see
    app/web/guest_permissions.py).
    """
    show_screen_time = guest_perms is None or guest_permissions.guest_can(guest_perms, "data:screen_time")
    show_bonus = guest_perms is None or guest_permissions.guest_can(guest_perms, "data:bonus_time")
    show_bedtime_schooltime = guest_perms is None or guest_permissions.guest_can(guest_perms, "data:bedtime_schooltime")
    show_location = guest_perms is None or guest_permissions.guest_can(guest_perms, "data:location")
    show_battery = guest_perms is None or guest_permissions.guest_can(guest_perms, "data:battery")
    location_tracking_enabled = settings_store.get_location_tracking_enabled(session)
    local_now = datetime.now(tz)
    local_today = local_now.date()

    summaries = []
    for child in children:
        snapshot = session.get(LatestSnapshot, child.id)
        data = snapshot.data if snapshot else {}
        apps_and_usage = (data or {}).get("apps_and_usage") or {}
        applied = (data or {}).get("applied_time_limits", {}) or {}
        device_names = device_names_from_snapshot(data)
        lock_states = applied.get("device_lock_states", {}) or {}

        devices = []
        total_used_minutes = 0
        for device_id, info in (applied.get("devices") or {}).items():
            used_minutes = info.get("used_minutes") or 0
            total_used_minutes += used_minutes
            daily_limit_enabled = bool(info.get("daily_limit_enabled"))
            devices.append({
                "name": device_names.get(device_id, f"Device {device_id[:8]}…"),
                "used_display": format_minutes(used_minutes),
                "remaining_display": (
                    format_minutes(info["remaining_minutes"]) if daily_limit_enabled else None
                ),
                "daily_limit_display": (
                    format_minutes(info["daily_limit_minutes"]) if daily_limit_enabled else None
                ),
                "bonus_display": (
                    format_minutes(info["bonus_minutes"]) if info.get("bonus_minutes") and show_bonus else None
                ),
                "bedtime_active": bool(info.get("bedtime_active")) and show_bedtime_schooltime,
                "schooltime_active": bool(info.get("schooltime_active")) and show_bedtime_schooltime,
                "locked": bool(lock_states.get(device_id)),
                "location": None,
                "battery_level": None,
                "battery_badge_class": None,
                # Used to decide default collapse state -- see
                # app/templates/status.html.
                "used_today": used_minutes > 0,
            })
        devices.sort(key=lambda d: d["name"].lower())

        summary_location = None
        location = _build_location_context((data or {}).get("location"), tz) if location_tracking_enabled else None
        app_usage_date = _latest_app_usage_date(apps_and_usage) or local_today
        if location:
            matched_device = None
            source_name = location.get("source_device_name")
            if source_name:
                for device in devices:
                    if device["name"].casefold() == source_name.casefold():
                        matched_device = device
                        break
            elif len(devices) == 1:
                matched_device = devices[0]

            public_location = {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "accuracy": location["accuracy"],
                "timestamp": location["timestamp"],
                "updated_display": location["updated_display"],
                "place_name": location["place_name"],
                "place_address": location["place_address"],
                "source_device_name": location["source_device_name"],
            }
            if matched_device is not None:
                if show_location:
                    matched_device["location"] = public_location
                if show_battery:
                    matched_device["battery_level"] = location["battery_level"]
                    matched_device["battery_badge_class"] = location["battery_badge_class"]
            elif show_location:
                summary_location = dict(public_location)
                if show_battery:
                    summary_location["battery_level"] = location["battery_level"]
                    summary_location["battery_badge_class"] = location["battery_badge_class"]

        summaries.append({
            "child": child,
            "total_used_display": format_minutes(total_used_minutes),
            "devices": devices,
            "location": summary_location,
            "app_usage_today": (
                _build_app_usage_for_day(apps_and_usage, app_usage_date)
                if show_screen_time else []
            ),
            "app_usage_hourly": (
                _build_hourly_app_usage_chart(
                    session,
                    child.id,
                    app_usage_date,
                    app_titles_from_snapshot({"apps_and_usage": apps_and_usage}),
                    local_now.hour,
                )
                if show_screen_time else None
            ),
        })
    return summaries


@router.get("/")
async def root(
    request: Request,
    session: Session = Depends(get_db),
    polled: bool = False,
    access=Depends(require_page_access("viewer", "page:status")),
):
    if not settings_store.is_setup_completed(session):
        return RedirectResponse("/setup", status_code=303)

    _user, is_guest = access
    guest_perms = guest_permissions.get_guest_permissions(session) if is_guest else None

    auth_client = build_auth_client()
    healthy = await auth_client.health_ok()
    cookies = await auth_client.get_cookies() if healthy else None
    enabled_children = session.exec(select(Child).where(Child.enabled == True)).all()  # noqa: E712
    if guest_perms is not None:
        allowed_ids = guest_permissions.guest_allowed_child_ids(session, guest_perms)
        enabled_children = [c for c in enabled_children if c.id in allowed_ids]
    children_count = len(enabled_children)

    # Most recent successful poll across all enabled children -- see
    # app/web/deps.py:last_poll_times. None if no child has ever
    # successfully polled yet (e.g. still awaiting first login/baseline).
    poll_times = last_poll_times(session)
    enabled_poll_times = [poll_times[c.id] for c in enabled_children if c.id in poll_times]
    last_poll_at = max(enabled_poll_times) if enabled_poll_times else None

    device_summaries = _build_device_summaries(
        session,
        enabled_children,
        guest_perms,
        get_effective_zone_info(request, session),
    )

    return render(request, "status.html", session, {
        "setup_completed": True,
        "polled": polled,
        "auth_healthy": healthy,
        "has_cookies": bool(cookies),
        "auth_ui_url": settings.familylink_auth_ui_url_with_key,
        "novnc_url": settings.familylink_auth_novnc_url,
        "children_count": children_count,
        "poll_interval_minutes": settings_store.get_poll_interval_minutes(session),
        "last_poll_at": last_poll_at,
        "device_summaries": device_summaries,
    })


@router.post("/poll-now")
async def poll_now(_access=Depends(require_page_access("contributor", "__never__"))):
    """Run a poll cycle immediately, instead of waiting for the next
    scheduled interval -- handy for verifying a fix or a fresh login works
    without sitting around for up to ~poll_interval_minutes.
    """
    await poll_once()
    return RedirectResponse("/?polled=true", status_code=303)
