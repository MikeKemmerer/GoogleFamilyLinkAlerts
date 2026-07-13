"""First-run setup wizard: auth status -> discover children -> alert config."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..config import settings
from ..db import settings_store
from ..db.models import Child
from ..familylink.api_client import FamilyLinkApiClient
from ..familylink.exceptions import AuthenticationError, FamilyLinkError
from .deps import build_auth_client, get_db, templates

router = APIRouter()


@router.get("/setup")
async def setup_get(request: Request, session: Session = Depends(get_db)):
    if settings_store.is_setup_completed(session):
        return RedirectResponse("/settings", status_code=303)

    auth_client = build_auth_client()
    healthy = await auth_client.health_ok()
    cookies = None
    auth_error = None
    if healthy:
        try:
            cookies = await auth_client.get_cookies()
        except AuthenticationError as err:
            auth_error = str(err)

    if not healthy or not cookies:
        return templates.TemplateResponse(request, "setup.html", {
            "stage": "auth",
            "healthy": healthy,
            "auth_error": auth_error,
            "auth_base_url": settings.familylink_auth_base_url,
            "novnc_url": settings.familylink_auth_novnc_url,
            "setup_completed": False,
        })

    existing_children = session.exec(select(Child)).all()
    if not existing_children:
        api_client = FamilyLinkApiClient(auth_client)
        try:
            await api_client.authenticate()
            discovered = await api_client.get_all_supervised_children()
        except (FamilyLinkError, ValueError) as err:
            return templates.TemplateResponse(request, "setup.html", {
                "stage": "discover_error",
                "error": str(err),
                "setup_completed": False,
            })
        return templates.TemplateResponse(request, "setup.html", {
            "stage": "children",
            "discovered": discovered,
            "setup_completed": False,
        })

    return templates.TemplateResponse(request, "setup.html", {
        "stage": "notify",
        "setup_completed": False,
    })


@router.post("/setup/children")
async def setup_children(request: Request, session: Session = Depends(get_db)):
    form = await request.form()
    ids = form.getlist("child_ids")
    names = form.getlist("child_names")
    for child_id, name in zip(ids, names):
        enabled = form.get(f"enabled_{child_id}") is not None
        session.add(Child(id=child_id, name=name, enabled=enabled))
    session.commit()
    return RedirectResponse("/setup", status_code=303)


@router.post("/setup/notify")
async def setup_notify(request: Request, session: Session = Depends(get_db)):
    form = await request.form()
    ntfy_server = form.get("ntfy_server", "").strip()
    ntfy_topic = form.get("ntfy_topic", "").strip()
    poll_interval_minutes = int(form.get("poll_interval_minutes", 20))

    settings_store.set_ntfy_config(session, ntfy_server, ntfy_topic)
    settings_store.set_poll_interval_minutes(session, poll_interval_minutes)
    settings_store.mark_setup_completed(session)

    return RedirectResponse("/settings", status_code=303)
