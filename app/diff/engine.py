"""Generic snapshot diffing.

Rather than hand-writing comparison logic per setting type (screen time vs.
app limits vs. bedtime, etc.), we flatten each snapshot dict into a set of
(field_path -> value) pairs and diff the two flat maps. This means adding a
brand new data source (e.g. once website_filter.py is implemented) requires
no changes here -- whatever shape it returns just becomes new field paths
automatically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChangeEvent:
    field_path: str
    old_value: Any
    new_value: Any


_SCALAR_TYPES = (str, int, float, bool, type(None))

# Field paths that are noisy but not actionable "permission changed" events,
# so we exclude them from diffing entirely (they're still stored in the raw
# snapshot for debugging -- only diffing skips them). Array indices are
# normalized to `[*]` before matching, since these are positional/protobuf
# style responses with no stable per-item key.
#
#   - `time_limit.*` is the *raw, unparsed* schedule-rules response from
#     Google's undocumented `timeLimit` endpoint (positional nested arrays,
#     e.g. `time_limit[1][0][1][0][0]`). The *effective*/applied state is
#     already surfaced in friendly form via `applied_time_limits.*` (see
#     app/familylink/api_client.py:_parse_applied_time_limits), so diffing
#     the raw config just produces cryptic, unresolvable noise until it gets
#     its own parser (tracked as follow-up work).
#   - The `apps_and_usage.*` entries below are device/app metadata that
#     changes constantly on its own (rotating signed thumbnail URLs, activity
#     heartbeats, a static capability-flag list) and isn't a "permission"
#     change a parent would want an alert for.
DEFAULT_IGNORED_PATH_PATTERNS: tuple[str, ...] = (
    r"^time_limit(\.|\[|$)",
    r"^apps_and_usage\.lastActivityRefreshTimestampMillis$",
    r"^apps_and_usage\.deviceInfo\[\*\]\.displayInfo\.thumbnail\.imageUrl$",
    r"^apps_and_usage\.deviceInfo\[\*\]\.displayInfo\.lastActivityTimeMillis$",
    r"^apps_and_usage\.deviceInfo\[\*\]\.capabilityInfo\.capabilities\[\*\]$",
)

_ARRAY_INDEX_RE = re.compile(r"\[\d+\]")


def _normalize_path_for_matching(path: str) -> str:
    """Replace array indices with `[*]` so ignore patterns match any index."""
    return _ARRAY_INDEX_RE.sub("[*]", path)


def is_ignored_path(path: str, patterns: tuple[str, ...] = DEFAULT_IGNORED_PATH_PATTERNS) -> bool:
    """Whether a field path should be excluded from diffing (see patterns above)."""
    normalized = _normalize_path_for_matching(path)
    return any(re.match(pattern, normalized) for pattern in patterns)


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict/list structure into {path: scalar_value}.

    Dict keys become `.key`, list indices become `[i]`, e.g.
    `apps[com.tiktok.android].blocked`.
    """
    flat: dict[str, Any] = {}
    if isinstance(data, dict):
        if not data:
            flat[prefix or "$"] = {}
            return flat
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flat.update(flatten(value, path))
    elif isinstance(data, (list, tuple)):
        if not data:
            flat[prefix or "$"] = []
            return flat
        for idx, value in enumerate(data):
            path = f"{prefix}[{idx}]"
            flat.update(flatten(value, path))
    elif isinstance(data, _SCALAR_TYPES):
        flat[prefix or "$"] = data
    else:
        # Fallback for unexpected types (e.g. datetime) -- compare by repr.
        flat[prefix or "$"] = str(data)
    return flat


def diff_snapshots(
    old: dict[str, Any] | None,
    new: dict[str, Any],
    ignore_noisy_paths: bool = True,
) -> list[ChangeEvent]:
    """Compare two raw snapshots and return every field-level change.

    `old` may be None -- every field in `new` is then reported as a change
    from None. Callers that want to treat a child's very first snapshot as a
    silent baseline (recommended -- see app/poller.py) should skip calling
    this at all when `old` is None, rather than rely on this function to
    suppress anything; this function always reports what differs between
    the two snapshots it's given.

    When `ignore_noisy_paths` is True (the default), paths matching
    `DEFAULT_IGNORED_PATH_PATTERNS` are excluded -- see that constant's
    docstring for why.
    """
    old_flat = flatten(old) if old is not None else {}
    new_flat = flatten(new)

    changes: list[ChangeEvent] = []
    all_paths = set(old_flat) | set(new_flat)
    for path in sorted(all_paths):
        if ignore_noisy_paths and is_ignored_path(path):
            continue
        old_value = old_flat.get(path)
        new_value = new_flat.get(path)
        if old_value != new_value:
            changes.append(ChangeEvent(field_path=path, old_value=old_value, new_value=new_value))
    return changes
