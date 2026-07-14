"""Change history / polling issue timeline."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from ..config import settings
from ..db.models import AppRule, Child, ChangeEvent, LatestSnapshot, PollFailure
from ..diff.labels import app_titles_from_snapshot, device_names_from_snapshot, humanize_field_path, humanize_value
from .deps import get_db, templates

router = APIRouter()


def _to_local(dt: datetime) -> datetime:
    """`detected_at`/`occurred_at` are stored as naive UTC (see
    app/db/models.py:_utcnow) -- attach UTC explicitly, then convert to the
    family's local timezone so the History page doesn't show a bare
    timestamp a parent has to mentally convert from UTC.
    """
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(settings.zone_info)


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

    # Device IDs are opaque strings (e.g. "aannnppa...") -- resolve them to
    # the friendly names Family Link shows (e.g. "Chromebook") using each
    # child's latest snapshot, so labels below can say "Chromebook: bedtime
    # starts" instead of a raw device ID.
    device_names_by_child: dict[str, dict[str, str]] = {}
    # Package names are stable but not human-friendly -- resolve to the
    # app's title the same way, so an app-blocked/unblocked event says
    # "Fortnite: blocked" instead of "com.epicgames.fortnite: blocked".
    # Starts from AppRule (persists titles even after an app is
    # uninstalled/no longer in the latest snapshot), then overlays the
    # freshest titles from the current snapshot where available.
    app_titles_by_child: dict[str, dict[str, str]] = {}
    for child in children:
        snapshot = session.get(LatestSnapshot, child.id)
        device_names_by_child[child.id] = device_names_from_snapshot(snapshot.data if snapshot else None)
        rules = session.exec(select(AppRule).where(AppRule.child_id == child.id)).all()
        titles = {rule.package_name: rule.title for rule in rules if rule.title}
        titles.update(app_titles_from_snapshot(snapshot.data if snapshot else None))
        app_titles_by_child[child.id] = titles

    rows = []
    for e in events:
        old_display = humanize_value(e.field_path, e.old_value, tz=settings.zone_info)
        new_display = humanize_value(e.field_path, e.new_value, tz=settings.zone_info)
        old_raw = _raw_display(e.old_value)
        new_raw = _raw_display(e.new_value)
        rows.append({
            "detected_at": _to_local(e.detected_at),
            "child_name": child_names.get(e.child_id, e.child_id),
            "field_path": e.field_path,
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
        {"occurred_at": _to_local(f.occurred_at), "kind": f.kind, "message": f.message}
        for f in failures
    ]

    return templates.TemplateResponse(request, "history.html", {
        "setup_completed": True,
        "rows": rows,
        "failures": failure_rows,
        "child_names": child_names,
    })
