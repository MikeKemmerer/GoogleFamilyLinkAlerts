"""Change history / polling issue timeline."""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from ..config import settings
from ..db.models import AppRule, Child, ChangeEvent, LatestSnapshot, PollFailure
from ..diff.labels import (
    app_icons_from_snapshot,
    app_titles_from_snapshot,
    device_names_from_snapshot,
    humanize_field_path,
    humanize_value,
)
from .deps import get_db, last_poll_times, templates, to_local

router = APIRouter()

_APP_PKG_FIELD_RE = re.compile(r"^apps_and_usage\.apps\[(?P<pkg>[^\]]+)\]\.")


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
async def history(request: Request, session: Session = Depends(get_db)):
    events = session.exec(select(ChangeEvent).order_by(ChangeEvent.detected_at.desc()).limit(200)).all()
    failures = session.exec(select(PollFailure).order_by(PollFailure.occurred_at.desc()).limit(50)).all()
    children = session.exec(select(Child)).all()
    child_names = {c.id: c.name for c in children}
    child_avatars = {c.id: c.avatar_url for c in children}

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
        old_display = humanize_value(e.field_path, e.old_value, tz=settings.zone_info)
        new_display = humanize_value(e.field_path, e.new_value, tz=settings.zone_info)
        old_raw = _raw_display(e.old_value)
        new_raw = _raw_display(e.new_value)
        pkg_match = _APP_PKG_FIELD_RE.match(e.field_path)
        icon_url = (
            app_icons_by_child.get(e.child_id, {}).get(pkg_match.group("pkg")) if pkg_match else None
        )
        rows.append({
            "detected_at": to_local(e.detected_at),
            "child_name": child_names.get(e.child_id, e.child_id),
            "child_avatar_url": child_avatars.get(e.child_id),
            "field_path": e.field_path,
            "icon_url": icon_url,
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
        {"occurred_at": to_local(f.occurred_at), "kind": f.kind, "message": f.message}
        for f in failures
    ]

    poll_times = last_poll_times(session)
    last_poll_by_child = {child.id: poll_times.get(child.id) for child in children}

    return templates.TemplateResponse(request, "history.html", {
        "setup_completed": True,
        "rows": rows,
        "failures": failure_rows,
        "child_names": child_names,
        "child_avatars": child_avatars,
        "last_poll_by_child": last_poll_by_child,
    })
