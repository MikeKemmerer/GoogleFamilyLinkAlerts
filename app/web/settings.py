"""Ongoing settings page: ntfy config, poll interval, per-child enable/disable."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import __version__
from ..config import settings
from ..db import settings_store
from ..db.models import AppRule, Child, LatestSnapshot
from ..notify.categories import CATEGORIES
from .deps import build_auth_client, get_db, templates

router = APIRouter()


@router.get("/settings")
async def settings_get(request: Request, session: Session = Depends(get_db), saved: bool = False, tz_error: bool = False):
    auth_client = build_auth_client()
    healthy = await auth_client.health_ok()
    cookies = await auth_client.get_cookies() if healthy else None

    ntfy_config = settings_store.get_ntfy_config(session)
    children = session.exec(select(Child)).all()
    app_rules = session.exec(select(AppRule)).all()
    blocked_apps_by_child: dict[str, list[AppRule]] = {}
    for rule in app_rules:
        blocked_apps_by_child.setdefault(rule.child_id, []).append(rule)
    for rules in blocked_apps_by_child.values():
        rules.sort(key=lambda r: r.title.lower())

    return templates.TemplateResponse(request, "settings.html", {
        "setup_completed": True,
        "saved": saved,
        "tz_error": tz_error,
        "auth_healthy": healthy,
        "has_cookies": bool(cookies),
        "auth_ui_url": settings.familylink_auth_ui_url_with_key,
        "novnc_url": settings.familylink_auth_novnc_url,
        "children": children,
        "blocked_apps_by_child": blocked_apps_by_child,
        "ntfy_server": ntfy_config[0] if ntfy_config else "",
        "ntfy_topic": ntfy_config[1] if ntfy_config else "",
        "poll_interval_minutes": settings_store.get_poll_interval_minutes(session),
        "notifications_enabled": settings_store.get_notifications_enabled(session),
        "notification_categories": CATEGORIES,
        "enabled_notification_categories": settings_store.get_enabled_notification_categories(session),
        "timezone": settings_store.get_timezone(session),
        "timezone_groups": settings_store.get_timezone_options(session),
        "theme": settings_store.get_theme(session),
        "app_version": __version__,
    })


@router.post("/settings")
async def settings_post(request: Request, session: Session = Depends(get_db)):
    form = await request.form()
    ntfy_server = form.get("ntfy_server", "").strip()
    ntfy_topic = form.get("ntfy_topic", "").strip()
    poll_interval_minutes = int(form.get("poll_interval_minutes", 20))
    notifications_enabled = form.get("notifications_enabled") is not None
    enabled_categories = {key for key in CATEGORIES if form.get(f"category_{key}") is not None}
    # The dropdown's "Other..." option switches to a free-text sibling
    # field (timezone_other) instead of submitting a made-up <option>
    # value, so a custom zone name is only ever read from there.
    timezone_selected = form.get("timezone", "").strip()
    timezone_input = form.get("timezone_other", "").strip() if timezone_selected == "__other__" else timezone_selected
    theme_input = form.get("theme", "").strip()

    settings_store.set_ntfy_config(session, ntfy_server, ntfy_topic)
    settings_store.set_poll_interval_minutes(session, poll_interval_minutes)
    settings_store.set_notifications_enabled(session, notifications_enabled)
    settings_store.set_enabled_notification_categories(session, enabled_categories)

    if theme_input in settings_store.VALID_THEMES:
        settings_store.set_theme(session, theme_input)

    tz_error = False
    if timezone_input:
        if settings_store.is_valid_timezone(timezone_input):
            settings_store.set_timezone(session, timezone_input)
        else:
            # Invalid input (typo, not a real IANA name) -- leave the
            # previously saved timezone untouched rather than silently
            # falling back to UTC, and flag it so the page can tell the
            # user why their change didn't take.
            tz_error = True

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        from ..poller import reschedule
        reschedule(scheduler, poll_interval_minutes)

    redirect_url = "/settings?saved=true"
    if tz_error:
        redirect_url += "&tz_error=true"
    return RedirectResponse(redirect_url, status_code=303)


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


@router.post("/settings/children/{child_id}/apps/{package_name}/toggle-always-blocked")
async def toggle_always_blocked(child_id: str, package_name: str, session: Session = Depends(get_db)):
    """Flip an app's `always_blocked` flag.

    When enabled, the poller (see app/poller.py:_enforce_always_blocked_apps)
    will immediately re-block this app on any poll where it's found enabled,
    instead of just alerting that it changed.
    """
    rule = session.get(AppRule, (child_id, package_name))
    if rule:
        rule.always_blocked = not rule.always_blocked
        session.add(rule)
        session.commit()
    return RedirectResponse("/settings", status_code=303)
