"""Ongoing settings page: ntfy config, poll interval, per-child enable/disable."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from .. import __version__, security
from ..config import settings
from ..db import settings_store
from ..db.models import AppRule, Child, LatestSnapshot, User
from ..notify.categories import CATEGORIES
from . import guest_permissions
from .deps import SESSION_KEY_USER_ID, build_auth_client, get_db, render, require_role

router = APIRouter()

# Cycled by the nav bar's quick theme-toggle button (see /toggle-theme
# below and app/templates/base.html) -- each click advances one step and
# persists immediately, independent of the explicit Auto/Light/Dark pill
# in the Settings -> Display section (which sets the value directly).
_THEME_CYCLE = ("auto", "light", "dark")


def _notification_categories_for_settings(session: Session) -> dict[str, str]:
    categories = dict(CATEGORIES)
    if not settings_store.get_location_tracking_enabled(session):
        categories.pop("location", None)
    return categories


@router.get("/account")
async def account_get(request: Request, session: Session = Depends(get_db), current_user: User = Depends(require_role("viewer"))):
    """A lightweight personal-preferences page reachable by any logged-in
    user (viewer included), unlike /settings which is contributor+ only --
    every account should be able to set their own display timezone/theme
    without needing Children/App-Rules access. See User.timezone/theme
    (app/db/models.py) and app/web/deps.py:get_effective_zone_info /
    render() for where these overrides actually take effect.
    """
    return render(request, "account.html", session, {
        "setup_completed": True,
        "timezone_groups": settings_store.get_timezone_options(session),
        "user_timezone": current_user.timezone or "",
        "user_theme": current_user.theme or "",
    })


@router.post("/account")
async def account_post(request: Request, session: Session = Depends(get_db), current_user: User = Depends(require_role("viewer"))):
    form = await request.form()
    timezone_selected = form.get("timezone", "").strip()
    timezone_input = form.get("timezone_other", "").strip() if timezone_selected == "__other__" else timezone_selected
    theme_input = form.get("theme", "").strip()

    if not timezone_input:
        current_user.timezone = None
    elif settings_store.is_valid_timezone(timezone_input):
        current_user.timezone = timezone_input

    current_user.theme = theme_input if theme_input in settings_store.VALID_THEMES else None
    session.add(current_user)
    session.commit()
    return RedirectResponse("/account?saved=true", status_code=303)


@router.get("/settings")
async def settings_get(
    request: Request,
    session: Session = Depends(get_db),
    saved: bool = False,
    tz_error: bool = False,
    current_user: User | None = Depends(require_role("contributor")),
):
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

    auth_enabled = settings_store.get_auth_enabled(session)
    # Contributors can reach this page (Children + App Rules), but every
    # other section (Connection/Notifications/Polling/Display/Access &
    # Users) is admin-only -- enforced both here (hides the markup) and on
    # the corresponding POST routes below (require_role("admin")), so a
    # contributor can't bypass the UI by posting the form directly.
    is_admin = (not auth_enabled) or (current_user is not None and current_user.role == "admin")

    users = session.exec(select(User)).all() if is_admin else []
    guest_view_enabled = settings_store.get_guest_view_enabled(session) if is_admin else False
    location_tracking_enabled = settings_store.get_location_tracking_enabled(session) if is_admin else False
    guest_perms = guest_permissions.get_guest_permissions(session) if is_admin else {}
    notification_categories = _notification_categories_for_settings(session)

    return render(request, "settings.html", session, {
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
        "notification_categories": notification_categories,
        "enabled_notification_categories": settings_store.get_enabled_notification_categories(session),
        "timezone": settings_store.get_timezone(session),
        "timezone_groups": settings_store.get_timezone_options(session),
        "app_version": __version__,
        "is_admin": is_admin,
        "access_auth_enabled": auth_enabled,
        "access_guest_view_enabled": guest_view_enabled,
        "location_tracking_enabled": location_tracking_enabled,
        "access_users": users,
        "access_valid_roles": ("admin", "contributor", "viewer"),
        "guest_categories": guest_permissions.FIXED_CATEGORIES,
        "guest_child_categories": guest_permissions.all_child_categories(session) if is_admin else [],
        "guest_permissions": guest_perms,
    })


@router.post("/settings")
async def settings_post(request: Request, session: Session = Depends(get_db), _admin: User | None = Depends(require_role("admin"))):
    form = await request.form()
    ntfy_server = form.get("ntfy_server", "").strip()
    ntfy_topic = form.get("ntfy_topic", "").strip()
    poll_interval_minutes = int(form.get("poll_interval_minutes", 20))
    notifications_enabled = form.get("notifications_enabled") is not None
    visible_categories = _notification_categories_for_settings(session)
    enabled_categories = {key for key in visible_categories if form.get(f"category_{key}") is not None}
    hidden_categories = set(CATEGORIES) - set(visible_categories)
    enabled_categories |= settings_store.get_enabled_notification_categories(session) & hidden_categories
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


@router.post("/toggle-theme")
async def toggle_theme(request: Request, session: Session = Depends(get_db)):
    """Advances the saved theme one step (auto -> light -> dark -> auto)
    and redirects back to whichever page the toggle was clicked from --
    used by the quick theme button in the nav bar (app/templates/base.html),
    which needs to persist immediately rather than being a client-only,
    per-tab flip that resets on the next navigation.
    """
    form = await request.form()
    next_path = form.get("next") or "/"
    if not next_path.startswith("/") or next_path.startswith("//"):
        # Guard against an open redirect via a crafted "next" value --
        # only ever allow relative paths within this app.
        next_path = "/"
    current = settings_store.get_theme(session)
    new_theme = _THEME_CYCLE[(_THEME_CYCLE.index(current) + 1) % len(_THEME_CYCLE)]
    settings_store.set_theme(session, new_theme)
    return RedirectResponse(next_path, status_code=303)


@router.post("/settings/location-tracking/toggle")
async def toggle_location_tracking(session: Session = Depends(get_db), _admin: User | None = Depends(require_role("admin"))):
    settings_store.set_location_tracking_enabled(session, not settings_store.get_location_tracking_enabled(session))
    return RedirectResponse("/settings?saved=true", status_code=303)


@router.post("/settings/children/{child_id}/toggle")
async def toggle_child(child_id: str, session: Session = Depends(get_db), _user: User | None = Depends(require_role("contributor"))):
    child = session.get(Child, child_id)
    if child:
        child.enabled = not child.enabled
        session.add(child)
        session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/children/{child_id}/toggle-auto-revoke-bonus-time")
async def toggle_auto_revoke_bonus_time(child_id: str, session: Session = Depends(get_db), _user: User | None = Depends(require_role("contributor"))):
    """Flip a child's `auto_revoke_bonus_time` flag.

    When enabled, the poller (see
    app/poller.py:_enforce_auto_revoke_bonus_time) will immediately cancel
    any active granted-bonus-time override on this child's devices on the
    very next poll, instead of leaving it active until it naturally expires.
    """
    child = session.get(Child, child_id)
    if child:
        child.auto_revoke_bonus_time = not child.auto_revoke_bonus_time
        session.add(child)
        session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/children/{child_id}/reset-baseline")
async def reset_baseline(child_id: str, session: Session = Depends(get_db), _user: User | None = Depends(require_role("contributor"))):
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
async def toggle_always_blocked(child_id: str, package_name: str, session: Session = Depends(get_db), _user: User | None = Depends(require_role("contributor"))):
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


# --- Access & Users -----------------------------------------------------
# Turning auth on is a two-step flow so nobody can lock themselves out:
# checking the box on /settings redirects here (GET) instead of flipping
# the setting directly; auth_enabled only becomes True once the admin
# account form below is submitted successfully.

@router.get("/settings/access/setup-admin")
async def access_setup_admin_get(request: Request, session: Session = Depends(get_db)):
    if settings_store.get_auth_enabled(session):
        return RedirectResponse("/settings", status_code=303)
    return render(request, "setup_admin.html", session, {"setup_completed": True})


@router.post("/settings/access/setup-admin")
async def access_setup_admin_post(request: Request, session: Session = Depends(get_db)):
    if settings_store.get_auth_enabled(session):
        return RedirectResponse("/settings", status_code=303)
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    if not username or not password:
        return render(request, "setup_admin.html", session, {
            "setup_completed": True,
            "error": "Username and password are required.",
        })
    try:
        password_hash = security.hash_password(password)
    except ValueError as exc:
        return render(request, "setup_admin.html", session, {"setup_completed": True, "error": str(exc)})

    admin = User(username=username, password_hash=password_hash, role="admin")
    session.add(admin)
    settings_store.set_auth_enabled(session, True)
    session.commit()

    # Log the brand-new admin straight in -- they just proved they know the
    # password they set 5 seconds ago, no need to make them log in again.
    request.session.clear()
    request.session[SESSION_KEY_USER_ID] = admin.id
    return RedirectResponse("/settings?saved=true", status_code=303)


@router.post("/settings/access/disable")
async def access_disable(session: Session = Depends(get_db), _admin: User | None = Depends(require_role("admin"))):
    """Turns the login requirement back off. Existing user accounts are
    left in place (not deleted) so re-enabling later doesn't require
    recreating everyone -- only the enforcement flag changes.
    """
    settings_store.set_auth_enabled(session, False)
    return RedirectResponse("/settings?saved=true", status_code=303)


@router.post("/settings/access/guest-view/toggle")
async def access_toggle_guest_view(session: Session = Depends(get_db), _admin: User | None = Depends(require_role("admin"))):
    settings_store.set_guest_view_enabled(session, not settings_store.get_guest_view_enabled(session))
    return RedirectResponse("/settings?saved=true", status_code=303)


@router.post("/settings/access/users")
async def access_create_user(request: Request, session: Session = Depends(get_db), _admin: User | None = Depends(require_role("admin"))):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    role = form.get("role", "viewer").strip()
    if role not in ("admin", "contributor", "viewer"):
        role = "viewer"

    if username and password:
        existing = session.exec(select(User).where(User.username == username)).first()
        if existing is None:
            try:
                password_hash = security.hash_password(password)
            except ValueError:
                password_hash = None
            if password_hash:
                session.add(User(username=username, password_hash=password_hash, role=role))
                session.commit()
    return RedirectResponse("/settings?saved=true", status_code=303)


@router.post("/settings/access/users/{user_id}/delete")
async def access_delete_user(user_id: str, session: Session = Depends(get_db), admin: User | None = Depends(require_role("admin"))):
    user = session.get(User, user_id)
    if user is not None:
        remaining_admins = session.exec(select(User).where(User.role == "admin")).all()
        # Guard against deleting the last admin account, which would leave
        # the "Access & Users" section unreachable by anyone (contributors/
        # viewers can't get there -- see require_role("admin") above).
        if not (user.role == "admin" and len(remaining_admins) <= 1):
            session.delete(user)
            session.commit()
    return RedirectResponse("/settings?saved=true", status_code=303)


@router.post("/settings/access/guest-permissions")
async def access_save_guest_permissions(request: Request, session: Session = Depends(get_db), _admin: User | None = Depends(require_role("admin"))):
    form = await request.form()
    enabled = {key for key in form.keys() if key.startswith(("page:", "data:", "history:", "child:"))}
    guest_permissions.set_guest_permissions(session, enabled)
    return RedirectResponse("/settings?saved=true", status_code=303)
