"""Periodic polling loop.

For every enabled child: fetch each Family Link data source, merge into one
snapshot dict, diff it against the last stored snapshot, persist any changes,
and send ntfy alerts. Auth/network failures are recorded and alerted on too,
instead of failing silently -- see app/db/models.py:PollFailure.
"""
from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select

from .config import settings
from .db import settings_store
from .db.models import AppRule, ChangeEvent, Child, LatestSnapshot, PollFailure
from .db.session import get_session
from .diff.engine import diff_snapshots
from .diff.labels import app_titles_from_snapshot, device_names_from_snapshot
from .familylink.api_client import FamilyLinkApiClient
from .familylink.auth_client import AuthClient
from .familylink.exceptions import AuthenticationError, FamilyLinkError, NetworkError, SessionExpiredError
from .familylink.website_filter import WebsiteFilterNotImplementedError, get_website_filter
from .notify.ntfy import NtfyClient, format_change_message, format_failure_message

_LOGGER = logging.getLogger(__name__)

# Randomized +/- jitter applied to the poll interval so requests don't look
# like a perfectly regular bot schedule.
_JITTER_FRACTION = 0.15

# Only log "website filter not implemented" once per process, not every cycle.
_website_filter_warned = False


def build_api_client() -> FamilyLinkApiClient:
    auth_client = AuthClient(
        base_url=settings.familylink_auth_base_url,
        api_key=settings.familylink_auth_api_key,
    )
    return FamilyLinkApiClient(auth_client)


async def _fetch_child_snapshot(client: FamilyLinkApiClient, child_id: str) -> dict[str, Any]:
    global _website_filter_warned
    snapshot: dict[str, Any] = {}

    snapshot["apps_and_usage"] = await client.get_apps_and_usage(child_id)
    snapshot["time_limit"] = await client.get_time_limit(child_id)
    snapshot["applied_time_limits"] = await client.get_applied_time_limits(child_id, tz=settings.zone_info)

    try:
        snapshot["website_filter"] = await get_website_filter(client, child_id)
    except WebsiteFilterNotImplementedError:
        if not _website_filter_warned:
            _LOGGER.info("Website filter monitoring not yet implemented -- skipping this data source.")
            _website_filter_warned = True

    return snapshot


def _record_failure(session: Session, kind: str, message: str) -> PollFailure:
    failure = PollFailure(kind=kind, message=message)
    session.add(failure)
    session.commit()
    session.refresh(failure)
    return failure


async def _maybe_notify_failure(session: Session, failure: PollFailure) -> None:
    ntfy_config = settings_store.get_ntfy_config(session)
    if not ntfy_config or not settings_store.get_notifications_enabled(session):
        return
    title, message = format_failure_message(failure.kind, failure.message)
    client = NtfyClient(*ntfy_config)
    if await client.send(title, message, priority="high", tags=["warning"]):
        failure.notified = True
        session.add(failure)
        session.commit()


async def _maybe_notify_changes(
    session: Session,
    child_name: str,
    events: list[ChangeEvent],
    device_names: dict[str, str] | None = None,
    app_titles: dict[str, str] | None = None,
) -> None:
    ntfy_config = settings_store.get_ntfy_config(session)
    if not ntfy_config or not settings_store.get_notifications_enabled(session):
        return
    client = NtfyClient(*ntfy_config)
    for event in events:
        title, message = format_change_message(
            child_name, event.field_path, event.old_value, event.new_value, device_names, app_titles
        )
        if await client.send(title, message, tags=["bell"]):
            event.notified = True
            session.add(event)
    session.commit()


def _iter_apps(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return snapshot.get("apps_and_usage", {}).get("apps", []) or []


# OEM/carrier package-name prefixes to exclude from blocked-app discovery --
# these are pre-installed system components a parent never chose to
# install, not real "block this app" decisions. Not exhaustive; add more
# here if other OEM/carrier bloat shows up on real data (see README's
# "Always-blocked apps" section). Deliberately does NOT include
# `com.google.*` -- Google apps (YouTube, Meet, etc.) are legitimate
# consumer apps a parent may genuinely want to enforce blocking on, even
# though they're pre-installed on most Android phones.
_SYSTEM_APP_PACKAGE_PREFIXES: tuple[str, ...] = (
    "com.samsung.",
    "com.sec.",
    "com.tmobile.",
    "com.verizon.",
    "com.att.",
    "com.sprint.",
    "com.lge.",
    "com.motorola.",
    "com.htc.",
)


def _is_preinstalled_system_app(app: dict[str, Any], package_name: str) -> bool:
    """Whether an app looks like OEM/carrier bloatware rather than something
    a parent actually chose to install and then block.

    Family Link hides a large number of pre-installed system components by
    default (Samsung Knox internals, "Galaxy Finder", "Game Home", etc.) --
    on real production data this was ~100 of 145 apps for one child. Most
    have `installTimeMillis == "0"` (never actually "installed" by anyone,
    just present since device setup), but some OEM/carrier apps get a real
    install timestamp anyway (e.g. Samsung's "Reminder", "Modes and
    Routines") -- so we also exclude by known OEM/carrier package prefixes.
    Excluding these keeps the Settings page's "Blocked apps" list limited
    to apps a parent would recognize and might want to enforce.
    """
    install_time = app.get("installTimeMillis")
    try:
        if int(install_time) == 0:
            return True
    except (TypeError, ValueError):
        pass
    return package_name.startswith(_SYSTEM_APP_PACKAGE_PREFIXES)


_APPS_HIDDEN_INDEX_RE = re.compile(r"^apps_and_usage\.apps\[(?P<index>\d+)\]\.supervisionSetting\.hidden$")


def _friendly_app_field_path(field_path: str, snapshot: dict[str, Any]) -> str:
    """Rewrite a raw, positional `apps_and_usage.apps[N].supervisionSetting.hidden`
    diff path to use the app's stable package name instead of its array
    index, e.g. `apps_and_usage.apps[com.epicgames.fortnite].supervisionSetting.hidden`.

    Family Link's app list can reorder between polls as apps are installed/
    removed (see diff/engine.py's docstring on positional array paths), so a
    bare numeric index isn't a reliable identifier across polls, and can't
    be resolved to a friendly app name without looking it up again. `pkg` in
    the rewritten path is looked up from `snapshot` -- the very same
    snapshot the diff that produced `field_path` was computed against, so
    the index-to-package mapping here is guaranteed correct for this event
    (unlike a later lookup against some other/future snapshot).
    """
    match = _APPS_HIDDEN_INDEX_RE.match(field_path)
    if not match:
        return field_path
    index = int(match.group("index"))
    apps = _iter_apps(snapshot)
    if index >= len(apps):
        return field_path
    package_name = FamilyLinkApiClient.get_app_package_name(apps[index])
    if not package_name:
        return field_path
    return f"apps_and_usage.apps[{package_name}].supervisionSetting.hidden"


def _sync_app_rules(session: Session, child_id: str, snapshot: dict[str, Any]) -> None:
    """Upsert an `AppRule` row for every app ever seen blocked for this child.

    Runs on every poll, independent of `always_blocked` -- this just builds
    the running "apps that have been blocked at least once" list the
    Settings page shows, so a parent can opt any of them into enforcement
    later. Newly-discovered apps default to `always_blocked=False`. Skips
    pre-installed system apps (see `_is_preinstalled_system_app`) so the
    list stays limited to apps a parent would recognize.
    """
    for app in _iter_apps(snapshot):
        package_name = FamilyLinkApiClient.get_app_package_name(app)
        if not package_name:
            continue
        hidden = bool((app.get("supervisionSetting") or {}).get("hidden"))
        if not hidden:
            continue
        if _is_preinstalled_system_app(app, package_name):
            continue
        title = app.get("title") or package_name
        rule = session.get(AppRule, (child_id, package_name))
        if rule is None:
            session.add(AppRule(child_id=child_id, package_name=package_name, title=title))
        elif rule.title != title:
            rule.title = title
            rule.updated_at = datetime.now(timezone.utc)
            session.add(rule)
    session.commit()


async def _enforce_always_blocked_apps(
    session: Session, client: FamilyLinkApiClient, child: Child, snapshot: dict[str, Any]
) -> None:
    """Immediately re-block any app a parent opted into "always blocked"
    if this poll's snapshot shows it currently enabled.

    IMPORTANT: callers must diff/record `snapshot` against the previous
    stored state *before* calling this -- it mutates `snapshot` in place to
    reflect the re-block. If called before diffing, the "someone re-enabled
    it" transition would never be visible (old and patched-new would look
    identical), which is exactly the bug this ordering avoids -- see
    app/poller.py:poll_once. The re-block itself is recorded as its own
    ChangeEvent (and a dedicated high-priority notification) here, using the
    same `apps[pkg].supervisionSetting.hidden` field path the organic diff
    would use, so both the "re-enabled" and "re-blocked" events show up
    under the same friendly label in the History page.
    """
    rules = session.exec(
        select(AppRule).where(AppRule.child_id == child.id, AppRule.always_blocked == True)  # noqa: E712
    ).all()
    if not rules:
        return
    apps_by_package = {
        FamilyLinkApiClient.get_app_package_name(app): app for app in _iter_apps(snapshot)
    }
    for rule in rules:
        app = apps_by_package.get(rule.package_name)
        if app is None:
            continue  # app no longer present (e.g. uninstalled) -- nothing to enforce
        supervision = app.setdefault("supervisionSetting", {}) or {}
        if supervision.get("hidden"):
            continue  # already blocked, nothing to do
        try:
            await client.block_app(child.id, rule.package_name)
        except (NetworkError, FamilyLinkError) as err:
            _LOGGER.warning("Failed to re-block %s for %s: %s", rule.package_name, child.name, err)
            continue
        _LOGGER.info("Re-blocked %s for %s (always-blocked rule)", rule.title or rule.package_name, child.name)
        supervision["hidden"] = True
        event = ChangeEvent(
            child_id=child.id,
            field_path=f"apps_and_usage.apps[{rule.package_name}].supervisionSetting.hidden",
            old_value=False,
            new_value=True,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        await _maybe_notify_enforcement(session, child.name, rule, event)


async def _maybe_notify_enforcement(session: Session, child_name: str, rule: AppRule, event: ChangeEvent) -> None:
    ntfy_config = settings_store.get_ntfy_config(session)
    if not ntfy_config or not settings_store.get_notifications_enabled(session):
        return
    client = NtfyClient(*ntfy_config)
    title = f"Family Link: re-blocked app for {child_name}"
    message = (
        f"{rule.title or rule.package_name} was re-enabled but is set to always be "
        f"blocked -- blocked it again automatically."
    )
    if await client.send(title, message, priority="high", tags=["no_entry_sign"]):
        event.notified = True
        session.add(event)
        session.commit()


async def poll_once() -> None:
    """Run a single poll cycle across all enabled children."""
    with get_session() as session:
        children = settings_store.all_enabled_children(session)
        if not children:
            _LOGGER.debug("No enabled children configured yet -- skipping poll cycle.")
            return

        client = build_api_client()
        try:
            await client.authenticate()
        except AuthenticationError as err:
            failure = _record_failure(session, "auth_required", str(err))
            await _maybe_notify_failure(session, failure)
            return
        except NetworkError as err:
            failure = _record_failure(session, "network_error", str(err))
            await _maybe_notify_failure(session, failure)
            return

        for child in children:
            try:
                new_snapshot = await _fetch_child_snapshot(client, child.id)
            except SessionExpiredError as err:
                failure = _record_failure(session, "session_expired", str(err))
                await _maybe_notify_failure(session, failure)
                return  # cookies are shared across children; stop this cycle
            except (NetworkError, FamilyLinkError) as err:
                failure = _record_failure(session, "network_error", f"{child.name}: {err}")
                await _maybe_notify_failure(session, failure)
                continue

            _sync_app_rules(session, child.id, new_snapshot)

            latest = session.get(LatestSnapshot, child.id)

            if latest is None:
                # First-ever snapshot for this child: Family Link's API only
                # exposes *current* state, not a real change history, so
                # there is nothing genuine to diff against. Treat this poll
                # as establishing a silent baseline -- store it and move on
                # -- rather than reporting every field as a "change from
                # None", which would flood ChangeEvent/ntfy with hundreds of
                # entries all timestamped "now" (see README "First poll").
                # Enforcement still runs here (there's no diff to protect,
                # so patching the snapshot in place is harmless) so an
                # always-blocked rule takes effect immediately even on a
                # brand new child/reinstall rather than waiting a cycle.
                await _enforce_always_blocked_apps(session, client, child, new_snapshot)
                session.add(LatestSnapshot(child_id=child.id, data=new_snapshot))
                session.commit()
                _LOGGER.info(
                    "Established baseline snapshot for %s -- future polls will report changes from here.",
                    child.name,
                )
                continue

            # Diff against the true, just-fetched state *before* enforcement
            # (below) has a chance to patch it -- otherwise an app that got
            # re-enabled and then immediately re-blocked within this same
            # poll cycle would never show up as a change at all (the old
            # stored value and the patched-new value would be identical).
            changes = diff_snapshots(latest.data, new_snapshot)
            events = [
                ChangeEvent(
                    child_id=child.id,
                    field_path=_friendly_app_field_path(c.field_path, new_snapshot),
                    old_value=c.old_value,
                    new_value=c.new_value,
                )
                for c in changes
            ]
            session.add_all(events)
            session.commit()
            for e in events:
                session.refresh(e)

            app_titles = app_titles_from_snapshot(new_snapshot)
            if events:
                _LOGGER.info("Detected %d change(s) for %s", len(events), child.name)
                device_names = device_names_from_snapshot(new_snapshot)
                await _maybe_notify_changes(session, child.name, events, device_names, app_titles)

            # Enforcement runs *after* diffing/notifying above so the
            # underlying "someone re-enabled it" transition is preserved as
            # its own recorded+notified event; it now patches `new_snapshot`
            # and records/notifies a distinct "re-blocked" event of its own.
            await _enforce_always_blocked_apps(session, client, child, new_snapshot)

            latest.data = new_snapshot
            latest.updated_at = datetime.now(timezone.utc)
            session.add(latest)
            session.commit()


def _jittered_interval_seconds(minutes: int) -> float:
    base = minutes * 60
    jitter = base * _JITTER_FRACTION
    return base + random.uniform(-jitter, jitter)


def start_scheduler() -> AsyncIOScheduler:
    """Create and start the background scheduler. Caller keeps a reference."""
    with get_session() as session:
        interval_minutes = settings_store.get_poll_interval_minutes(session)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_once,
        trigger=IntervalTrigger(seconds=_jittered_interval_seconds(interval_minutes)),
        id="familylink_poll",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _LOGGER.info("Poller scheduled every ~%d minutes", interval_minutes)
    return scheduler


def reschedule(scheduler: AsyncIOScheduler, interval_minutes: int) -> None:
    """Update the poll job's interval, e.g. after the user changes it in Settings."""
    scheduler.reschedule_job(
        "familylink_poll",
        trigger=IntervalTrigger(seconds=_jittered_interval_seconds(interval_minutes)),
    )
    _LOGGER.info("Poller rescheduled to every ~%d minutes", interval_minutes)
