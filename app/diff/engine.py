"""Generic snapshot diffing.

Rather than hand-writing comparison logic per setting type (screen time vs.
app limits vs. bedtime, etc.), we flatten each snapshot dict into a set of
(field_path -> value) pairs and diff the two flat maps. This means adding a
brand new data source (e.g. once website_filter.py is implemented) requires
no changes here -- whatever shape it returns just becomes new field paths
automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChangeEvent:
    field_path: str
    old_value: Any
    new_value: Any


_SCALAR_TYPES = (str, int, float, bool, type(None))


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


def diff_snapshots(old: dict[str, Any] | None, new: dict[str, Any]) -> list[ChangeEvent]:
    """Compare two raw snapshots and return every field-level change.

    `old` may be None (first-ever snapshot for a child) -- in that case
    every field in `new` is reported as a change from None, so history
    starts with a baseline of "everything was just observed for the first
    time" rather than silently skipping the first poll.
    """
    old_flat = flatten(old) if old is not None else {}
    new_flat = flatten(new)

    changes: list[ChangeEvent] = []
    all_paths = set(old_flat) | set(new_flat)
    for path in sorted(all_paths):
        old_value = old_flat.get(path)
        new_value = new_flat.get(path)
        if old_value != new_value:
            changes.append(ChangeEvent(field_path=path, old_value=old_value, new_value=new_value))
    return changes
