"""Canonical list of guest-visibility toggles + helpers for reading them.

The actual on/off state lives in the GuestPermission table (one row per
category, see app/db/models.py). This module is the single source of truth
for which *fixed* category keys exist (page/data-category toggles) and how
to look up per-child toggle keys, so the Settings UI and the route-gating
dependencies (app/web/deps.py) never drift out of sync on spelling.

Everything defaults to disabled (a missing row == False) -- see
get_guest_permissions() -- so turning on guest mode never silently exposes
anything an admin hasn't deliberately opted in.
"""
from __future__ import annotations

from sqlmodel import Session, select

from ..db.models import Child, GuestPermission

# (category key, human label, help text) for the fixed (non-per-child)
# toggles shown in Settings -> Access & Users -> Guest visibility.
FIXED_CATEGORIES: list[tuple[str, str, str]] = [
    ("page:status", "Status page", "Show the Status page at all to guests."),
    ("page:history", "History page", "Show the History page at all to guests."),
    ("data:screen_time", "Screen time & limits", "Daily limit, used/remaining time per device."),
    ("data:bonus_time", "Bonus/extra time", "Granted or auto-revoked bonus time."),
    ("data:app_blocking", "App blocking", "Which apps are blocked/always-blocked."),
    ("data:bedtime_schooltime", "Bedtime & school time", "Bedtime/school-time windows and active state."),
    ("data:location", "Device location", "Last-known location map for a device."),
    ("data:battery", "Battery level", "Device battery percentage."),
    ("history:show_actor", "Who/what triggered a change", "Show enforcement/system-triggered detail on History rows, not just 'something changed'."),
    ("history:full_pagination", "Full history pagination", "Let guests page back through older history instead of only the most recent page."),
]

CHILD_CATEGORY_PREFIX = "child:"


def child_category(child_id: str) -> str:
    return f"{CHILD_CATEGORY_PREFIX}{child_id}"


def get_guest_permissions(session: Session) -> dict[str, bool]:
    """All currently-set GuestPermission rows as a {category: enabled} dict.

    A category with no row is treated as disabled by callers (see
    guest_can()) -- this function only returns rows that have been
    explicitly saved at least once.
    """
    rows = session.exec(select(GuestPermission)).all()
    return {row.category: row.enabled for row in rows}


def guest_can(permissions: dict[str, bool], category: str) -> bool:
    """Whether a guest session may see `category` -- missing == False."""
    return permissions.get(category, False)


def set_guest_permissions(session: Session, enabled_categories: set[str]) -> None:
    """Replace the full set of enabled categories in one go (used by the
    Settings -> Guest visibility form, which submits every checkbox's
    current state at once rather than toggling one at a time).
    """
    existing = {row.category: row for row in session.exec(select(GuestPermission)).all()}
    all_known = {key for key, _, _ in FIXED_CATEGORIES}
    all_known |= {row.category for row in existing.values()}
    all_known |= enabled_categories
    for category in all_known:
        wanted = category in enabled_categories
        row = existing.get(category)
        if row is None:
            session.add(GuestPermission(category=category, enabled=wanted))
        elif row.enabled != wanted:
            row.enabled = wanted
            session.add(row)
    session.commit()


def all_child_categories(session: Session) -> list[tuple[str, str]]:
    """(category_key, child_name) for every known child, for rendering the
    per-child guest-visibility checkboxes in Settings.
    """
    children = session.exec(select(Child)).all()
    return [(child_category(c.id), c.name) for c in children]


def guest_allowed_child_ids(session: Session, permissions: dict[str, bool] | None = None) -> set[str]:
    """Which child IDs a guest may see at all, per their per-child toggle.

    Used by status.py/history.py to filter the child list down before
    rendering, so a guest never sees so much as a name for a child whose
    `child:<id>` toggle isn't explicitly on.
    """
    if permissions is None:
        permissions = get_guest_permissions(session)
    children = session.exec(select(Child)).all()
    return {c.id for c in children if guest_can(permissions, child_category(c.id))}
