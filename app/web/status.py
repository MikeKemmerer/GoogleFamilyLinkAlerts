"""Root status/dashboard page."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..config import settings
from ..db import settings_store
from ..db.models import Child, LatestSnapshot
from ..diff.labels import device_names_from_snapshot, format_minutes
from ..poller import poll_once
from .deps import build_auth_client, get_db, last_poll_times, templates

router = APIRouter()


def _build_device_summaries(session: Session, children: list[Child]) -> list[dict]:
    """Per-child "screen time today" summary for the Status page: total
    minutes used today (summed across all their devices) plus a per-device
    breakdown, sourced from the same `applied_time_limits.devices.*` data
    already captured every poll (see
    app/familylink/api_client.py:_parse_applied_time_limits) -- nothing new
    to fetch, just a live view of what's already stored.
    """
    summaries = []
    for child in children:
        snapshot = session.get(LatestSnapshot, child.id)
        data = snapshot.data if snapshot else {}
        applied = (data or {}).get("applied_time_limits", {}) or {}
        device_names = device_names_from_snapshot(data)
        lock_states = applied.get("device_lock_states", {}) or {}

        devices = []
        total_used_minutes = 0
        for device_id, info in (applied.get("devices") or {}).items():
            used_minutes = info.get("used_minutes") or 0
            total_used_minutes += used_minutes
            daily_limit_enabled = bool(info.get("daily_limit_enabled"))
            devices.append({
                "name": device_names.get(device_id, f"Device {device_id[:8]}…"),
                "used_display": format_minutes(used_minutes),
                "remaining_display": (
                    format_minutes(info["remaining_minutes"]) if daily_limit_enabled else None
                ),
                "daily_limit_display": (
                    format_minutes(info["daily_limit_minutes"]) if daily_limit_enabled else None
                ),
                "bonus_display": format_minutes(info["bonus_minutes"]) if info.get("bonus_minutes") else None,
                "bedtime_active": bool(info.get("bedtime_active")),
                "schooltime_active": bool(info.get("schooltime_active")),
                "locked": bool(lock_states.get(device_id)),
                # Used to decide default collapse state -- see
                # app/templates/status.html.
                "used_today": used_minutes > 0,
            })
        devices.sort(key=lambda d: d["name"].lower())

        summaries.append({
            "child": child,
            "total_used_display": format_minutes(total_used_minutes),
            "devices": devices,
        })
    return summaries


@router.get("/")
async def root(request: Request, session: Session = Depends(get_db), polled: bool = False):
    if not settings_store.is_setup_completed(session):
        return RedirectResponse("/setup", status_code=303)

    auth_client = build_auth_client()
    healthy = await auth_client.health_ok()
    cookies = await auth_client.get_cookies() if healthy else None
    enabled_children = session.exec(select(Child).where(Child.enabled == True)).all()  # noqa: E712
    children_count = len(enabled_children)

    # Most recent successful poll across all enabled children -- see
    # app/web/deps.py:last_poll_times. None if no child has ever
    # successfully polled yet (e.g. still awaiting first login/baseline).
    poll_times = last_poll_times(session)
    enabled_poll_times = [poll_times[c.id] for c in enabled_children if c.id in poll_times]
    last_poll_at = max(enabled_poll_times) if enabled_poll_times else None

    device_summaries = _build_device_summaries(session, enabled_children)

    return templates.TemplateResponse(request, "status.html", {
        "setup_completed": True,
        "polled": polled,
        "auth_healthy": healthy,
        "has_cookies": bool(cookies),
        "auth_ui_url": settings.familylink_auth_ui_url_with_key,
        "novnc_url": settings.familylink_auth_novnc_url,
        "children_count": children_count,
        "poll_interval_minutes": settings_store.get_poll_interval_minutes(session),
        "last_poll_at": last_poll_at,
        "device_summaries": device_summaries,
    })


@router.post("/poll-now")
async def poll_now():
    """Run a poll cycle immediately, instead of waiting for the next
    scheduled interval -- handy for verifying a fix or a fresh login works
    without sitting around for up to ~poll_interval_minutes.
    """
    await poll_once()
    return RedirectResponse("/?polled=true", status_code=303)
