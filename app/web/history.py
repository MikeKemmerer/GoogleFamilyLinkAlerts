"""Change history / polling issue timeline."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from ..db.models import Child, ChangeEvent, LatestSnapshot, PollFailure
from ..diff.labels import device_names_from_snapshot, humanize_field_path, humanize_value
from .deps import get_db, templates

router = APIRouter()


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
            "detected_at": e.detected_at,
            "child_name": child_names.get(e.child_id, e.child_id),
            "field_path": e.field_path,
            "label": humanize_field_path(e.field_path, device_names_by_child.get(e.child_id, {})),
            "old_display": humanize_value(e.field_path, e.old_value),
            "new_display": humanize_value(e.field_path, e.new_value),
        }
        for e in events
    ]

    return templates.TemplateResponse(request, "history.html", {
        "setup_completed": True,
        "rows": rows,
        "failures": failures,
        "child_names": child_names,
    })
