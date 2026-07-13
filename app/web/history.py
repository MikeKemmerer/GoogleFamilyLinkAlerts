"""Change history / polling issue timeline."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from ..db.models import Child, ChangeEvent, PollFailure
from .deps import get_db, templates

router = APIRouter()


@router.get("/history")
async def history(request: Request, session: Session = Depends(get_db)):
    events = session.exec(select(ChangeEvent).order_by(ChangeEvent.detected_at.desc()).limit(200)).all()
    failures = session.exec(select(PollFailure).order_by(PollFailure.occurred_at.desc()).limit(50)).all()
    children = session.exec(select(Child)).all()
    child_names = {c.id: c.name for c in children}

    return templates.TemplateResponse(request, "history.html", {
        "setup_completed": True,
        "events": events,
        "failures": failures,
        "child_names": child_names,
    })
