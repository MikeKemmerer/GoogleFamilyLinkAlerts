"""Root status/dashboard page."""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select
from zoneinfo import ZoneInfo

from ..config import settings
from ..db import settings_store
from ..db.models import Child, LatestSnapshot
from ..diff.labels import app_titles_from_snapshot, device_names_from_snapshot, format_minutes
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

_APP_USAGE_COLOR_VARS = [
    "--app-usage-color-1",
    "--app-usage-color-2",
    "--app-usage-color-3",
    "--app-usage-color-4",
    "--app-usage-color-5",
    "--app-usage-color-6",
    "--app-usage-color-7",
    "--app-usage-color-8",
]


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


def _parse_usage_seconds(value: Any) -> float | None:
    """Parse Family Link's app-usage duration strings like ``"123.4s"``."""
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        seconds = float(value[:-1])
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def _format_usage_duration(seconds: float) -> str:
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


def _app_usage_color_var(package_name: str) -> str:
    digest = hashlib.sha1(package_name.encode("utf-8")).digest()
    return _APP_USAGE_COLOR_VARS[digest[0] % len(_APP_USAGE_COLOR_VARS)]


def _build_app_usage_for_day(apps_and_usage: dict[str, Any] | None, target_date: date) -> list[dict[str, Any]]:
    """Aggregate ``appUsageSessions`` for one local calendar day."""
    if not isinstance(apps_and_usage, dict):
        return []

    app_titles = app_titles_from_snapshot({"apps_and_usage": apps_and_usage})
    totals: dict[str, float] = {}
    for session in apps_and_usage.get("appUsageSessions") or []:
        if not isinstance(session, dict):
            continue
        session_date = session.get("date") or {}
        if (
            session_date.get("year") != target_date.year
            or session_date.get("month") != target_date.month
            or session_date.get("day") != target_date.day
        ):
            continue
        app_id = session.get("appId") or {}
        if not isinstance(app_id, dict):
            continue
        package_name = app_id.get("androidAppPackageName")
        if not isinstance(package_name, str) or not package_name:
            continue
        seconds = _parse_usage_seconds(session.get("usage"))
        if seconds is None:
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
            "duration_display": _format_usage_duration(seconds),
            "width_pct": (seconds / total_seconds) * 100,
            "color_var": _app_usage_color_var(package_name),
        }
        for package_name, seconds in totals.items()
    ]
    usage.sort(key=lambda item: (-item["seconds"], item["app_title"].casefold(), item["package_name"]))
    return usage


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
    local_today = datetime.now(tz).date()

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
                _build_app_usage_for_day(apps_and_usage, local_today)
                if show_screen_time else []
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
