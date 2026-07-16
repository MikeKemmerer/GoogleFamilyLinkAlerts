"""Shared FastAPI dependencies for the web UI."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlmodel import Session, select

from ..config import settings
from ..db import settings_store
from ..db.models import LatestSnapshot, User
from ..db.session import get_engine

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

# The icon sprite is inlined directly into every page (instead of being
# referenced as an external file via `<use href="/static/icons.svg#id">`)
# because cross-document external `<use>` references are unreliable on some
# mobile browsers (notably iOS Safari) -- symbols can silently fail to
# render, especially inside elements that aren't visible at first paint, or
# once a browser has cached an older copy of the sprite file from before a
# given icon was added. Inlining removes the extra network/cache round trip
# entirely and guarantees every symbol referenced by `#icon-x` is always
# present in the same document. `app/static/icons.svg` remains the single
# source of truth for the icon definitions; this just reads it once at
# import time and exposes it to templates as `icon_sprite()`.
_ICON_SPRITE_PATH = Path(__file__).resolve().parent.parent / "static" / "icons.svg"
_icon_sprite_markup = Markup(_ICON_SPRITE_PATH.read_text(encoding="utf-8"))
templates.env.globals["icon_sprite"] = lambda: _icon_sprite_markup

# Session keys used in the signed cookie (see app/main.py's SessionMiddleware).
SESSION_KEY_USER_ID = "user_id"
SESSION_KEY_GUEST = "is_guest"

# Weakest-to-strongest so `require_role("contributor")` can accept both
# "contributor" and "admin". Kept here (not in db/models.py) since it's a
# web-layer authorization concept, not a schema concept.
_ROLE_RANK = {"viewer": 0, "contributor": 1, "admin": 2}


def get_db() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def build_auth_client():
    from ..familylink.auth_client import AuthClient
    return AuthClient(base_url=settings.familylink_auth_base_url, api_key=settings.familylink_auth_api_key)


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    """Convert a stored UTC timestamp (naive or aware -- see
    app/db/models.py:_utcnow) to `tz` (the family's configured *display*
    timezone -- see app/db/settings_store.py:get_zone_info) so the web UI
    never shows a bare timestamp a parent has to mentally convert from UTC.
    """
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(tz)


def last_poll_times(session: Session) -> dict[str, datetime]:
    """Per-child "last successful poll" time, localized.

    `LatestSnapshot.updated_at` is bumped every time a child's snapshot is
    successfully fetched and stored (see app/poller.py:poll_once) -- both on
    a brand new baseline poll and on every steady-state poll -- so it's an
    accurate proxy for "when did we last successfully hear from Family Link
    for this child", independent of whether *other* children in the same
    cycle failed (each child's fetch is independent; see poll_once's
    per-child try/except).
    """
    tz = settings_store.get_zone_info(session)
    snapshots = session.exec(select(LatestSnapshot)).all()
    return {s.child_id: to_local(s.updated_at, tz) for s in snapshots}


def get_current_user(request: Request, session: Session) -> User | None:
    """The logged-in User for this request, or None if not logged in / auth
    disabled / this is a guest session (guests have no User row -- see
    is_guest_session).
    """
    user_id = request.session.get(SESSION_KEY_USER_ID)
    if not user_id:
        return None
    return session.get(User, user_id)


def is_guest_session(request: Request) -> bool:
    return bool(request.session.get(SESSION_KEY_GUEST))


def current_role(request: Request, session: Session) -> str | None:
    """The effective role for this request: an actual User's role, "guest"
    for a guest session, or None if nobody is logged in at all. Always None
    when auth is disabled -- callers should check
    settings_store.get_auth_enabled() first (see require_role) rather than
    relying on this to distinguish "auth off" from "not logged in".
    """
    if is_guest_session(request):
        return "guest"
    user = get_current_user(request, session)
    return user.role if user else None


def require_role(min_role: str):
    """Dependency factory gating a route to `min_role` or stronger
    (viewer < contributor < admin), OR allowing it through untouched when
    `auth_enabled` is False -- so an install that never turns auth on sees
    zero behavior change on any route that uses this. Guests never satisfy
    this dependency (guest visibility is checked separately and more
    granularly via require_page_access) -- if a guest hits a
    `require_role`-gated route, redirect to login same as an anonymous
    visitor, since guest permissions are page/category-specific, not role
    based.
    """
    async def _dependency(request: Request, session: Session = Depends(get_db)):
        if not settings_store.get_auth_enabled(session):
            return None
        if is_guest_session(request):
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        user = get_current_user(request, session)
        if user is None:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        if _ROLE_RANK.get(user.role, -1) < _ROLE_RANK.get(min_role, 99):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return _dependency


def require_page_access(min_role: str, guest_category: str):
    """Like require_role, but also lets a guest session through when their
    admin-configured `guest_category` toggle (see
    app/web/guest_permissions.py) is on -- used for the two whole-page
    routes a guest can ever reach (Status: "page:status", History:
    "page:history"). Every other `require_role`-gated route (Settings)
    has no guest_category and simply isn't reachable by guests at all.

    Returns `(user, is_guest)` -- both None/False when auth is disabled.
    """
    async def _dependency(request: Request, session: Session = Depends(get_db)):
        if not settings_store.get_auth_enabled(session):
            return (None, False)
        if is_guest_session(request):
            from . import guest_permissions
            if not settings_store.get_guest_view_enabled(session):
                raise HTTPException(status_code=303, headers={"Location": "/login"})
            perms = guest_permissions.get_guest_permissions(session)
            if not guest_permissions.guest_can(perms, guest_category):
                raise HTTPException(status_code=303, headers={"Location": "/login"})
            return (None, True)
        user = get_current_user(request, session)
        if user is None:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        if _ROLE_RANK.get(user.role, -1) < _ROLE_RANK.get(min_role, 99):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return (user, False)
    return _dependency


def get_effective_zone_info(request: Request, session: Session) -> ZoneInfo:
    """Like settings_store.get_zone_info, but overlaid with the logged-in
    user's own timezone preference if they've set one (see
    app/db/models.py:User.timezone) -- falls back to the global display
    timezone when auth is off, nobody's logged in, or the user hasn't set
    their own override. Guests always see the global default (no per-guest
    preference storage).
    """
    if settings_store.get_auth_enabled(session):
        user = get_current_user(request, session)
        if user and user.timezone:
            try:
                return ZoneInfo(user.timezone)
            except (Exception,):
                pass
    return settings_store.get_zone_info(session)


def render(request, template_name: str, session: Session, context: dict):
    """`templates.TemplateResponse` wrapper that always injects context every
    page needs regardless of which route renders it -- theme (see
    settings_store.get_theme, overridden by the current user's own
    preference if logged in and set), plus auth/guest state so base.html
    can show the right nav (login/logout/username+role or nothing at all
    when auth is disabled). Every route that extends base.html should go
    through this rather than calling `templates.TemplateResponse` directly,
    so a shared context value can never be silently missing from one page
    but not another (which is exactly how the Settings page's theme picker
    used to have no effect anywhere except the Settings page itself).
    """
    auth_enabled = settings_store.get_auth_enabled(session)
    current_user = get_current_user(request, session) if auth_enabled else None
    guest = auth_enabled and is_guest_session(request)
    theme = settings_store.get_theme(session)
    if current_user and current_user.theme:
        theme = current_user.theme
    merged = {
        "theme": theme,
        "auth_enabled": auth_enabled,
        "current_user": current_user,
        "is_guest": guest,
        **context,
    }
    return templates.TemplateResponse(request, template_name, merged)
