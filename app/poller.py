"""Periodic polling loop.

For every enabled child: fetch each Family Link data source, merge into one
snapshot dict, diff it against the last stored snapshot, persist any changes,
and send ntfy alerts. Auth/network failures are recorded and alerted on too,
instead of failing silently -- see app/db/models.py:PollFailure.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session

from .config import settings
from .db import settings_store
from .db.models import ChangeEvent, LatestSnapshot, PollFailure
from .db.session import get_session
from .diff.engine import diff_snapshots
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
    snapshot["applied_time_limits"] = await client.get_applied_time_limits(child_id)

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
    if not ntfy_config:
        return
    title, message = format_failure_message(failure.kind, failure.message)
    client = NtfyClient(*ntfy_config)
    if await client.send(title, message, priority="high", tags=["warning"]):
        failure.notified = True
        session.add(failure)
        session.commit()


async def _maybe_notify_changes(session: Session, child_name: str, events: list[ChangeEvent]) -> None:
    ntfy_config = settings_store.get_ntfy_config(session)
    if not ntfy_config:
        return
    client = NtfyClient(*ntfy_config)
    for event in events:
        title, message = format_change_message(child_name, event.field_path, event.old_value, event.new_value)
        if await client.send(title, message, tags=["bell"]):
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

            latest = session.get(LatestSnapshot, child.id)
            old_snapshot = latest.data if latest else None
            changes = diff_snapshots(old_snapshot, new_snapshot)

            if latest:
                latest.data = new_snapshot
                session.add(latest)
            else:
                session.add(LatestSnapshot(child_id=child.id, data=new_snapshot))

            events = [
                ChangeEvent(
                    child_id=child.id,
                    field_path=c.field_path,
                    old_value=c.old_value,
                    new_value=c.new_value,
                )
                for c in changes
            ]
            session.add_all(events)
            session.commit()
            for e in events:
                session.refresh(e)

            if events:
                _LOGGER.info("Detected %d change(s) for %s", len(events), child.name)
                await _maybe_notify_changes(session, child.name, events)


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
