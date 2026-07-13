"""Ongoing settings page: ntfy config, poll interval, per-child enable/disable."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..config import settings
from ..db import settings_store
from ..db.models import Child, LatestSnapshot
from .deps import build_auth_client, get_db, templates

router = APIRouter()


@router.get("/settings")
async def settings_get(request: Request, session: Session = Depends(get_db), saved: bool = False):
    auth_client = build_auth_client()
    healthy = await auth_client.health_ok()
    cookies = await auth_client.get_cookies() if healthy else None

    ntfy_config = settings_store.get_ntfy_config(session)
    children = session.exec(select(Child)).all()

    return templates.TemplateResponse(request, "settings.html", {
        "setup_completed": True,
        "saved": saved,
        "auth_healthy": healthy,
        "has_cookies": bool(cookies),
        "auth_ui_url": settings.familylink_auth_ui_url_with_key,
        "novnc_url": settings.familylink_auth_novnc_url,
        "children": children,
        "ntfy_server": ntfy_config[0] if ntfy_config else "",
        "ntfy_topic": ntfy_config[1] if ntfy_config else "",
        "poll_interval_minutes": settings_store.get_poll_interval_minutes(session),
    })


@router.post("/settings")
async def settings_post(request: Request, session: Session = Depends(get_db)):
    form = await request.form()
    ntfy_server = form.get("ntfy_server", "").strip()
    ntfy_topic = form.get("ntfy_topic", "").strip()
    poll_interval_minutes = int(form.get("poll_interval_minutes", 20))

    settings_store.set_ntfy_config(session, ntfy_server, ntfy_topic)
    settings_store.set_poll_interval_minutes(session, poll_interval_minutes)

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        from ..poller import reschedule
        reschedule(scheduler, poll_interval_minutes)

    return RedirectResponse("/settings?saved=true", status_code=303)


@router.post("/settings/children/{child_id}/toggle")
async def toggle_child(child_id: str, session: Session = Depends(get_db)):
    child = session.get(Child, child_id)
    if child:
        child.enabled = not child.enabled
        session.add(child)
        session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/children/{child_id}/reset-baseline")
async def reset_baseline(child_id: str, session: Session = Depends(get_db)):
    """Delete the stored snapshot for a child so the next poll re-establishes
    a silent baseline instead of diffing against stale data.

    Useful after making a bunch of manual changes at once (e.g. a big
    cleanup pass in Family Link) that would otherwise show up as a wall of
    unrelated change notifications on the next poll.
    """
    snapshot = session.get(LatestSnapshot, child_id)
    if snapshot:
        session.delete(snapshot)
        session.commit()
    return RedirectResponse("/settings", status_code=303)
