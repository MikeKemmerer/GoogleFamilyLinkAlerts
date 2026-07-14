"""Human-friendly labels/values for the History page and ntfy messages.

Family Link's raw API responses are undocumented, positional, and full of
internal metadata. app/diff/engine.py already excludes the worst offenders
from being diffed at all (see DEFAULT_IGNORED_PATH_PATTERNS). This module
turns what's *left* into something a parent can actually read:

- Fields from the already-parsed `applied_time_limits.*` source (see
  app/familylink/api_client.py:_parse_applied_time_limits) get a curated,
  hand-written label, with device IDs resolved to friendly device names
  when possible (e.g. "Chromebook: bedtime starts" instead of
  "applied_time_limits.devices.aannnppa....bedtime_window.start_ms").
- Everything else (mainly `apps_and_usage.apps[N].*`, which isn't parsed
  into friendly keys yet) falls back to a generic humanizer that turns the
  raw dotted/bracketed path into "Title Case → Title Case" segments --
  still not a full translation, but far more readable than the raw path.
- Values are rendered too: None -> "—", booleans -> Yes/No, and known
  millisecond-epoch timestamp fields (anything ending in `_ms` or
  `Millis`) -> a local date/time string instead of a raw big number.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_DEVICE_ID_GROUP = r"(?P<device_id>[A-Za-z0-9_-]+)"

# (regex matching the raw field_path, human label template). `{device}` is
# substituted by humanize_field_path() with a resolved friendly device name
# (or a shortened device ID if it can't be resolved).
_KNOWN_LABELS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^applied_time_limits\.bedtime_enabled_today$"), "Bedtime enabled today"),
    (re.compile(r"^applied_time_limits\.schooltime_enabled_today$"), "School time enabled today"),
    (re.compile(rf"^applied_time_limits\.device_lock_states\.{_DEVICE_ID_GROUP}$"), "{device}: locked"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.total_allowed_minutes$"),
     "{device}: total allowed screen time"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.used_minutes$"),
     "{device}: screen time used"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.remaining_minutes$"),
     "{device}: screen time remaining"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.daily_limit_enabled$"),
     "{device}: daily limit enabled"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.daily_limit_minutes$"),
     "{device}: daily limit"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.bedtime_window\.start_ms$"),
     "{device}: bedtime starts"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.bedtime_window\.end_ms$"),
     "{device}: bedtime ends"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.schooltime_window\.start_ms$"),
     "{device}: school time starts"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.schooltime_window\.end_ms$"),
     "{device}: school time ends"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.bedtime_active$"),
     "{device}: bedtime active right now"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.schooltime_active$"),
     "{device}: school time active right now"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.bonus_minutes$"),
     "{device}: bonus time granted"),
    (re.compile(rf"^applied_time_limits\.devices\.{_DEVICE_ID_GROUP}\.bonus_override_id$"),
     "{device}: bonus time override id"),
]

# Field-path suffixes that hold millisecond-epoch timestamps.
_MS_TIMESTAMP_SUFFIXES = ("_ms", "Millis")

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")
_INDEX_RE = re.compile(r"\[(\d+)\]")


def _humanize_segment(segment: str) -> str:
    """`dailyUsageLimitMins` -> `Daily Usage Limit Mins`; `apps[9]` -> `Apps #9`."""
    segment = _INDEX_RE.sub(lambda m: f" #{m.group(1)}", segment)
    segment = segment.replace("_", " ")
    segment = _CAMEL_RE.sub(" ", segment)
    return " ".join(word.capitalize() for word in segment.split())


def _generic_label(field_path: str) -> str:
    return " → ".join(_humanize_segment(part) for part in field_path.split("."))


def humanize_field_path(field_path: str, device_names: dict[str, str] | None = None) -> str:
    """Best-effort human-readable label for a raw diff field path."""
    device_names = device_names or {}
    for pattern, template in _KNOWN_LABELS:
        match = pattern.match(field_path)
        if match:
            device_id = match.groupdict().get("device_id")
            if device_id:
                device = device_names.get(device_id, f"Device {device_id[:8]}…")
            else:
                device = ""
            return template.format(device=device)
    return _generic_label(field_path)


def humanize_value(field_path: str, value: Any, tz: Any = None) -> str:
    """Best-effort human-readable rendering of a diffed old/new value.

    `tz` (a `datetime.tzinfo`, e.g. `app.config.settings.zone_info`) is used
    to render millisecond-epoch timestamp fields in the family's local time
    rather than the container's system time (which is UTC in production) --
    otherwise displayed bedtime/school-time clock times are off by the UTC
    offset from what's actually configured in Family Link.
    """
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)) and any(field_path.endswith(suffix) for suffix in _MS_TIMESTAMP_SUFFIXES):
        try:
            dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            if tz is not None:
                dt = dt.astimezone(tz)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            return str(value)
    return str(value)


def device_names_from_snapshot(snapshot_data: dict[str, Any] | None) -> dict[str, str]:
    """Build a {device_id: friendly_name} map from a stored snapshot's
    `apps_and_usage.deviceInfo` list, for resolving device IDs in labels."""
    names: dict[str, str] = {}
    if not snapshot_data:
        return names
    for device in snapshot_data.get("apps_and_usage", {}).get("deviceInfo", []) or []:
        device_id = device.get("deviceId")
        friendly_name = (device.get("displayInfo") or {}).get("friendlyName")
        if device_id and friendly_name:
            names[device_id] = friendly_name
    return names
