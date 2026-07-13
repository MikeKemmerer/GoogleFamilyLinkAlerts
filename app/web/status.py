"""Root status/dashboard page."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..config import settings
from ..db import settings_store
from ..db.models import Child
from ..poller import poll_once
from .deps import build_auth_client, get_db, templates

router = APIRouter()


@router.get("/")
async def root(request: Request, session: Session = Depends(get_db), polled: bool = False):
    if not settings_store.is_setup_completed(session):
        return RedirectResponse("/setup", status_code=303)

    auth_client = build_auth_client()
    healthy = await auth_client.health_ok()
    cookies = await auth_client.get_cookies() if healthy else None
    children_count = len(session.exec(select(Child).where(Child.enabled == True)).all())  # noqa: E712

    return templates.TemplateResponse(request, "status.html", {
        "setup_completed": True,
        "polled": polled,
        "auth_healthy": healthy,
        "has_cookies": bool(cookies),
        "auth_ui_url": settings.familylink_auth_ui_url_with_key,
        "novnc_url": settings.familylink_auth_novnc_url,
        "children_count": children_count,
        "poll_interval_minutes": settings_store.get_poll_interval_minutes(session),
    })


@router.post("/poll-now")
async def poll_now():
    """Run a poll cycle immediately, instead of waiting for the next
    scheduled interval -- handy for verifying a fix or a fresh login works
    without sitting around for up to ~poll_interval_minutes.
    """
    await poll_once()
    return RedirectResponse("/?polled=true", status_code=303)
