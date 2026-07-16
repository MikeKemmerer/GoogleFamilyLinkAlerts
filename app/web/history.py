"""Change history / polling issue timeline."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, func, select

from ..db import settings_store
from ..db.models import AppRule, Child, ChangeEvent, LatestSnapshot, PollFailure
from ..diff.labels import (
    app_icons_from_snapshot,
    app_titles_from_snapshot,
    device_names_from_snapshot,
    humanize_field_path,
    humanize_value,
)
from ..notify.categories import CATEGORIES, category_for_field_path
from . import guest_permissions
from .deps import get_db, get_effective_zone_info, last_poll_times, render, require_page_access, to_local

router = APIRouter()

# Detected-changes list page size. Kept fairly generous since each row is a
# single line (collapsed) -- most reading happens on page 1 anyway, this
# just keeps very long histories from turning into one giant table.
_PAGE_SIZE = 50

# Maps each notification category (app.notify.categories.CATEGORIES) to a
# self-hosted Lucide icon id (see app/static/icons.svg) shown next to each
# History row's change label.
_CATEGORY_ICONS: dict[str, str] = {
    "app_blocking": "phone-off",
    "screen_time": "clock",
    "bonus_time": "gift",
    "bedtime_schooltime": "bed",
    "location": "map-pin",
    "device_lock": "lock",
    "polling_issues": "ban",
    "other": "bell",
}

_CATEGORY_LABELS: dict[str, str] = {
    "app_blocking": "App blocking",
    "screen_time": "Screen time",
    "bonus_time": "Bonus time",
    "bedtime_schooltime": "Bedtime / school time",
    "location": "Location",
    "device_lock": "Device lock",
    "other": "Other",
}

_CHANGE_CATEGORY_KEYS: tuple[str, ...] = tuple(key for key in CATEGORIES if key != "polling_issues")
_LOCATION_EVENT_WINDOW_SECONDS = 1.0
_LOCATION_FIELDS = (
    "latitude",
    "longitude",
    "accuracy",
    "timestamp",
    "place_name",
    "place_address",
    "battery_level",
    "source_device_name",
)


_APP_PKG_FIELD_RE = re.compile(r"^apps_and_usage\.apps\[(?P<pkg>[^\]]+)\]\.")

# Maps a notification category (app.notify.categories.CATEGORIES) to the
# guest-visibility data category that gates it (see
# app/web/guest_permissions.py). Categories with no entry here
# ("polling_issues", "other") are never shown to a guest, regardless of
# their toggles -- both are operational/ungrouped noise, not something an
# admin would deliberately want to expose to a guest.
_GUEST_DATA_CATEGORY: dict[str, str] = {
    "app_blocking": "data:app_blocking",
    "screen_time": "data:screen_time",
    "bonus_time": "data:bonus_time",
    "bedtime_schooltime": "data:bedtime_schooltime",
    "location": "data:location",
    "device_lock": "data:screen_time",
}


def _raw_display(value: Any) -> str:
    """Unformatted rendering of an old/new value, shown only when a row is
    expanded -- lets a parent see the exact underlying value (e.g. the raw
    millisecond epoch or minute count) behind a humanized display like a
    local date/time or "1h 15m", without cluttering the collapsed row."""
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_json(value: Any) -> str:
    return json.dumps(value).replace("</", "<\\/")


def _location_state_from_snapshot(snapshot: LatestSnapshot | None) -> dict[str, Any]:
    location = (snapshot.data if snapshot else {}).get("location") if snapshot else None
    if not isinstance(location, dict):
        return {}
    return {field: location.get(field) for field in _LOCATION_FIELDS if field in location}


def _group_location_events(events: list[ChangeEvent]) -> list[list[ChangeEvent]]:
    groups: list[list[ChangeEvent]] = []
    current: list[ChangeEvent] = []
    current_fields: set[str] = set()
    last_detected_at = None
    for event in events:
        field_name = event.field_path.split(".", 1)[1]
        starts_new_group = (
            current
            and (
                (event.detected_at - last_detected_at).total_seconds() > _LOCATION_EVENT_WINDOW_SECONDS
                or field_name in current_fields
            )
        )
        if starts_new_group:
            groups.append(current)
            current = []
            current_fields = set()
        current.append(event)
        current_fields.add(field_name)
        last_detected_at = event.detected_at
    if current:
        groups.append(current)
    return groups


def _location_fix_from_state(state: dict[str, Any], detected_at: datetime, tz) -> dict[str, Any] | None:
    latitude = state.get("latitude")
    longitude = state.get("longitude")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    timestamp_dt = _parse_iso_datetime(state.get("timestamp")) or detected_at
    place_name = state.get("place_name")
    place_address = state.get("place_address")
    return {
        "timestamp": timestamp_dt.isoformat(),
        "timestamp_display": to_local(timestamp_dt, tz).strftime("%Y-%m-%d %H:%M"),
        "latitude": float(latitude),
        "longitude": float(longitude),
        "accuracy": state.get("accuracy"),
        "place_name": place_name,
        "place_address": place_address,
        "place_display": place_name or place_address or "Unknown location",
        "source_device_name": state.get("source_device_name"),
        "battery_level": state.get("battery_level"),
    }


def _reconstruct_location_fixes(
    snapshot: LatestSnapshot | None,
    location_events: list[ChangeEvent],
    tz,
) -> list[dict[str, Any]]:
    if not location_events:
        return []
    groups = _group_location_events(location_events)
    state = _location_state_from_snapshot(snapshot)
    if not state:
        for event in groups[-1]:
            state[event.field_path.split(".", 1)[1]] = event.new_value
    fixes_desc: list[dict[str, Any]] = []
    for group in reversed(groups):
        fix = _location_fix_from_state(state, group[-1].detected_at, tz)
        if fix is not None:
            fixes_desc.append(fix)
        for event in reversed(group):
            field_name = event.field_path.split(".", 1)[1]
            if event.old_value is None:
                state.pop(field_name, None)
            else:
                state[field_name] = event.old_value
    fixes_desc.reverse()
    return fixes_desc


def _category_filter_options() -> list[dict[str, str]]:
    return [
        {
            "key": key,
            "label": _CATEGORY_LABELS.get(key, CATEGORIES.get(key, key).title()),
            "description": CATEGORIES[key],
            "icon": _CATEGORY_ICONS.get(key, "bell"),
        }
        for key in _CHANGE_CATEGORY_KEYS
    ]


@router.get("/history")
async def history(
    request: Request,
    session: Session = Depends(get_db),
    child_id: str = "",
    category: str = "",
    page: int = 1,
    view: str = "",
    access=Depends(require_page_access("viewer", "page:history")),
):
    page = max(page, 1)
    selected_category = category if category in _CHANGE_CATEGORY_KEYS else ""
    history_mode = "location" if view == "location" else "list"

    _user, is_guest = access
    guest_perms = guest_permissions.get_guest_permissions(session) if is_guest else None
    if guest_perms is not None:
        allowed_child_ids = guest_permissions.guest_allowed_child_ids(session, guest_perms)
        if child_id and child_id not in allowed_child_ids and history_mode == "location":
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        # A guest explicitly filtering to a child they're not allowed to see
        # (e.g. a hand-edited URL) falls back to "no filter" rather than
        # leaking that the child exists via an empty-but-distinct result.
        if child_id and child_id not in allowed_child_ids:
            child_id = ""
        show_actor = guest_permissions.guest_can(guest_perms, "history:show_actor")
        full_pagination = guest_permissions.guest_can(guest_perms, "history:full_pagination")
    else:
        allowed_child_ids = None
        show_actor = True
        full_pagination = True

    children = session.exec(select(Child)).all()
    if allowed_child_ids is not None:
        children = [c for c in children if c.id in allowed_child_ids]
    child_names = {c.id: c.name for c in children}
    child_avatars = {c.id: c.avatar_url for c in children}
    tz = get_effective_zone_info(request, session)
    if history_mode == "location":
        if not child_id:
            history_mode = "list"
        elif guest_perms is not None and not guest_permissions.guest_can(guest_perms, "data:location"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        elif child_id not in child_names:
            raise HTTPException(status_code=404, detail="Child not found")

    # Device IDs are opaque strings (e.g. "aannnppa...") -- resolve them to
    # the friendly names Family Link shows (e.g. "Chromebook") using each
    # child's latest snapshot, so labels below can say "Chromebook: bedtime
    # starts" instead of a raw device ID.
    device_names_by_child: dict[str, dict[str, str]] = {}
    # Package names are stable but not human-friendly -- resolve to the
    # app's title (and icon) the same way, so an app-blocked/unblocked event
    # says "Fortnite: blocked" (with Fortnite's icon) instead of
    # "com.epicgames.fortnite: blocked". Starts from AppRule (persists
    # titles/icons even after an app is uninstalled/no longer in the latest
    # snapshot), then overlays the freshest values from the current
    # snapshot where available.
    app_titles_by_child: dict[str, dict[str, str]] = {}
    app_icons_by_child: dict[str, dict[str, str]] = {}
    for child in children:
        snapshot = session.get(LatestSnapshot, child.id)
        device_names_by_child[child.id] = device_names_from_snapshot(snapshot.data if snapshot else None)
        rules = session.exec(select(AppRule).where(AppRule.child_id == child.id)).all()
        titles = {rule.package_name: rule.title for rule in rules if rule.title}
        titles.update(app_titles_from_snapshot(snapshot.data if snapshot else None))
        app_titles_by_child[child.id] = titles
        icons = {rule.package_name: rule.icon_url for rule in rules if rule.icon_url}
        icons.update(app_icons_from_snapshot(snapshot.data if snapshot else None))
        app_icons_by_child[child.id] = icons

    location_history_href = None
    can_view_location_history = child_id in child_names and (
        guest_perms is None or guest_permissions.guest_can(guest_perms, "data:location")
    )
    if can_view_location_history:
        location_count_query = (
            select(func.count())
            .select_from(ChangeEvent)
            .where(ChangeEvent.child_id == child_id)
            .where(ChangeEvent.field_path.like("location.%"))
        )
        if session.exec(location_count_query).one():
            location_history_href = f"/history?child_id={child_id}&view=location"

    if history_mode == "location":
        location_events = session.exec(
            select(ChangeEvent)
            .where(ChangeEvent.child_id == child_id)
            .where(ChangeEvent.field_path.like("location.%"))
            .order_by(ChangeEvent.detected_at.asc(), ChangeEvent.id.asc())
        ).all()
        location_fixes = _reconstruct_location_fixes(session.get(LatestSnapshot, child_id), location_events, tz)
        return render(request, "history.html", session, {
            "setup_completed": True,
            "history_mode": "location",
            "children": children,
            "selected_child_id": child_id,
            "selected_category": selected_category,
            "category_filters": _category_filter_options(),
            "child_names": child_names,
            "child_avatars": child_avatars,
            "location_history_href": location_history_href,
            "location_history": {
                "child_id": child_id,
                "child_name": child_names.get(child_id, child_id),
                "child_avatar_url": child_avatars.get(child_id),
                "fix_count": len(location_fixes),
                "fixes": location_fixes,
                "fixes_json": _safe_json(location_fixes),
            },
        })

    events_query = select(ChangeEvent).order_by(ChangeEvent.detected_at.desc(), ChangeEvent.id.desc())
    if child_id:
        events_query = events_query.where(ChangeEvent.child_id == child_id)
    elif allowed_child_ids is not None:
        events_query = events_query.where(ChangeEvent.child_id.in_(allowed_child_ids))
    events = session.exec(events_query).all()
    if guest_perms is not None:
        events = [
            e for e in events
            if guest_permissions.guest_can(guest_perms, _GUEST_DATA_CATEGORY.get(category_for_field_path(e.field_path), ""))
        ]
    if selected_category:
        events = [e for e in events if category_for_field_path(e.field_path) == selected_category]
    total_events = len(events)
    total_pages = max((total_events + _PAGE_SIZE - 1) // _PAGE_SIZE, 1)
    if not full_pagination:
        # "history:full_pagination" off means a guest only ever sees the
        # single most recent page -- older pages aren't reachable at all,
        # not just hidden behind a nav control (a hand-edited ?page= is
        # still clamped back to 1 below).
        total_pages = 1
    # Clamp a too-high page (e.g. a stale bookmark after events were pruned)
    # back onto the last real page instead of silently rendering an empty
    # table with no indication anything went wrong.
    page = min(page, total_pages)
    events = events[(page - 1) * _PAGE_SIZE:page * _PAGE_SIZE]
    failures = (
        []
        if guest_perms is not None
        else session.exec(select(PollFailure).order_by(PollFailure.occurred_at.desc()).limit(50)).all()
    )

    rows = []
    for e in events:
        category_key = category_for_field_path(e.field_path)
        old_display = humanize_value(e.field_path, e.old_value, tz=tz)
        new_display = humanize_value(e.field_path, e.new_value, tz=tz)
        old_raw = _raw_display(e.old_value)
        new_raw = _raw_display(e.new_value)
        pkg_match = _APP_PKG_FIELD_RE.match(e.field_path)
        icon_url = (
            app_icons_by_child.get(e.child_id, {}).get(pkg_match.group("pkg")) if pkg_match else None
        )
        rows.append({
            "detected_at": to_local(e.detected_at, tz),
            "child_name": child_names.get(e.child_id, e.child_id),
            "child_avatar_url": child_avatars.get(e.child_id),
            "field_path": e.field_path,
            "icon_url": icon_url,
            "category_icon": _CATEGORY_ICONS.get(category_key, "bell"),
            "category_label": _CATEGORY_LABELS.get(category_key, category_key.replace("_", " ").title()),
            "label": humanize_field_path(
                e.field_path, device_names_by_child.get(e.child_id, {}), app_titles_by_child.get(e.child_id, {})
            ),
            "old_display": old_display,
            "new_display": new_display,
            # Only surface the raw underlying value in the expanded detail
            # when it actually differs from the humanized display (e.g. a
            # raw ms epoch behind a formatted date, or a raw minute count
            # behind "1h 15m") -- otherwise it'd just repeat what's already
            # shown in the Old/New columns.
            "old_raw": old_raw if old_raw != old_display else None,
            "new_raw": new_raw if new_raw != new_display else None,
            "notified": e.notified,
        })


    failure_rows = [
        {"occurred_at": to_local(f.occurred_at, tz), "kind": f.kind, "message": f.message}
        for f in failures
    ]

    poll_times = last_poll_times(session)
    last_poll_by_child = {child.id: poll_times.get(child.id) for child in children}

    return render(request, "history.html", session, {
        "setup_completed": True,
        "history_mode": "list",
        "rows": rows,
        "failures": failure_rows,
        "children": children,
        "selected_child_id": child_id,
        "selected_category": selected_category,
        "category_filters": _category_filter_options(),
        "location_history_href": location_history_href,
        "child_names": child_names,
        "child_avatars": child_avatars,
        "last_poll_by_child": last_poll_by_child,
        "page": page,
        "total_pages": total_pages,
        "total_events": total_events,
        "show_actor": show_actor,
    })
