"""Change history / polling issue timeline."""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
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
from ..notify.categories import category_for_field_path
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
    "device_lock": "lock",
    "polling_issues": "ban",
    "other": "bell",
}


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


@router.get("/history")
async def history(
    request: Request,
    session: Session = Depends(get_db),
    child_id: str = "",
    page: int = 1,
    access=Depends(require_page_access("viewer", "page:history")),
):
    page = max(page, 1)

    _user, is_guest = access
    guest_perms = guest_permissions.get_guest_permissions(session) if is_guest else None
    if guest_perms is not None:
        allowed_child_ids = guest_permissions.guest_allowed_child_ids(session, guest_perms)
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

    count_query = select(func.count()).select_from(ChangeEvent)
    if child_id:
        count_query = count_query.where(ChangeEvent.child_id == child_id)
    elif allowed_child_ids is not None:
        count_query = count_query.where(ChangeEvent.child_id.in_(allowed_child_ids))
    total_events = session.exec(count_query).one()
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

    events_query = select(ChangeEvent).order_by(ChangeEvent.detected_at.desc())
    if child_id:
        events_query = events_query.where(ChangeEvent.child_id == child_id)
    elif allowed_child_ids is not None:
        events_query = events_query.where(ChangeEvent.child_id.in_(allowed_child_ids))
    events_query = events_query.offset((page - 1) * _PAGE_SIZE).limit(_PAGE_SIZE)
    events = session.exec(events_query).all()
    if guest_perms is not None:
        # Category filtering happens after the page-sized fetch (not in
        # SQL) since a category is derived from field_path, not stored --
        # this can make a guest's page slightly shorter than _PAGE_SIZE
        # when some rows are filtered out, which is an acceptable trade-off
        # for keeping the category taxonomy in one place
        # (app/notify/categories.py) rather than duplicating it into SQL.
        events = [
            e for e in events
            if guest_permissions.guest_can(guest_perms, _GUEST_DATA_CATEGORY.get(category_for_field_path(e.field_path), ""))
        ]
    failures = (
        []
        if guest_perms is not None
        else session.exec(select(PollFailure).order_by(PollFailure.occurred_at.desc()).limit(50)).all()
    )
    children = session.exec(select(Child)).all()
    if allowed_child_ids is not None:
        children = [c for c in children if c.id in allowed_child_ids]
    child_names = {c.id: c.name for c in children}
    child_avatars = {c.id: c.avatar_url for c in children}
    tz = get_effective_zone_info(request, session)

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

    rows = []
    for e in events:
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
            "category_icon": _CATEGORY_ICONS.get(category_for_field_path(e.field_path), "bell"),
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
        "rows": rows,
        "failures": failure_rows,
        "children": children,
        "selected_child_id": child_id,
        "child_names": child_names,
        "child_avatars": child_avatars,
        "last_poll_by_child": last_poll_by_child,
        "page": page,
        "total_pages": total_pages,
        "total_events": total_events,
        "show_actor": show_actor,
    })
