"""ntfy (https://ntfy.sh) push notification client.

Used for two kinds of alerts:
1. Detected settings changes (app/diff/engine.py output).
2. Poll failures -- auth expired, familylink-auth unreachable, etc. -- so
   the user finds out promptly instead of silently losing visibility.
"""
from __future__ import annotations

import logging

import httpx

from ..diff.labels import humanize_field_path, humanize_value

_LOGGER = logging.getLogger(__name__)


class NtfyClient:
    def __init__(self, server_url: str, topic: str, timeout: float = 10.0) -> None:
        self._url = f"{server_url.rstrip('/')}/{topic}"
        self._timeout = timeout

    async def send(self, title: str, message: str, priority: str = "default", tags: list[str] | None = None) -> bool:
        """Send a notification. Returns True on success, False otherwise.

        Failures are logged but never raised -- a broken ntfy config should
        not crash the poller; it should just mean the user misses an alert
        (which the web UI's change history will still show).
        """
        headers = {
            "Title": title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = ",".join(tags)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, content=message.encode("utf-8"), headers=headers)
            if resp.status_code >= 300:
                _LOGGER.warning("ntfy returned HTTP %s for %s", resp.status_code, self._url)
                return False
            return True
        except httpx.HTTPError as err:
            _LOGGER.warning("Failed to send ntfy notification: %s", err)
            return False


def format_change_message(
    child_name: str,
    field_path: str,
    old_value,
    new_value,
    device_names: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Build a human-readable (title, message) pair for a settings change."""
    title = f"Family Link change: {child_name}"
    label = humanize_field_path(field_path, device_names)
    old_display = humanize_value(field_path, old_value)
    new_display = humanize_value(field_path, new_value)
    message = f"{label}\n{old_display} -> {new_display}"
    return title, message


def format_failure_message(kind: str, detail: str) -> tuple[str, str]:
    title = "Family Link Alerts: polling issue"
    message = f"[{kind}] {detail}\nCheck the app's status page to re-authenticate if needed."
    return title, message
