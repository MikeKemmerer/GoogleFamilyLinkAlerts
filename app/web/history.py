"""Change history / polling issue timeline."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from ..config import settings
from ..db.models import Child, ChangeEvent, LatestSnapshot, PollFailure
from ..diff.labels import device_names_from_snapshot, humanize_field_path, humanize_value
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
    for child in children:
        snapshot = session.get(LatestSnapshot, child.id)
        device_names_by_child[child.id] = device_names_from_snapshot(snapshot.data if snapshot else None)

    rows = [
        {
            "detected_at": _to_local(e.detected_at),
            "child_name": child_names.get(e.child_id, e.child_id),
            "field_path": e.field_path,
            "label": humanize_field_path(e.field_path, device_names_by_child.get(e.child_id, {})),
            "old_display": humanize_value(e.field_path, e.old_value, tz=settings.zone_info),
            "new_display": humanize_value(e.field_path, e.new_value, tz=settings.zone_info),
        }
        for e in events
    ]

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
