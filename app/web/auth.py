"""Login/logout/guest routes for this app's own optional web-UI auth.

Entirely separate from the Google account authentication handled by the
familylink-auth container (app/familylink/auth_client.py) -- this is just
gating access to *this app's* own pages, off by default (see
app/db/settings_store.py:get_auth_enabled).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import security
from ..db import settings_store
from ..db.models import User
from .deps import SESSION_KEY_GUEST, SESSION_KEY_USER_ID, get_db, render

router = APIRouter()


@router.get("/login")
async def login_get(request: Request, session: Session = Depends(get_db), error: bool = False):
    if not settings_store.get_auth_enabled(session):
        # Auth isn't on -- there's nothing to log into, don't dead-end here.
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", session, {
        "error": error,
        "guest_view_enabled": settings_store.get_guest_view_enabled(session),
    })


@router.post("/login")
async def login_post(request: Request, session: Session = Depends(get_db)):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")

    user = session.exec(select(User).where(User.username == username)).first()
    if user is None or not security.verify_password(password, user.password_hash):
        return RedirectResponse("/login?error=true", status_code=303)

    request.session.clear()
    request.session[SESSION_KEY_USER_ID] = user.id
    return RedirectResponse("/", status_code=303)


@router.get("/login/guest")
async def login_guest(request: Request, session: Session = Depends(get_db)):
    if not settings_store.get_auth_enabled(session) or not settings_store.get_guest_view_enabled(session):
        return RedirectResponse("/login", status_code=303)
    request.session.clear()
    request.session[SESSION_KEY_GUEST] = True
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
