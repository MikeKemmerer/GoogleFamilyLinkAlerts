"""Tests for the timezone-sensitivity of appliedTimeLimits parsing.

Google's schedule "day"/hour values are in the family's local time, not
UTC (see app/familylink/api_client.py:_parse_applied_time_limits). These
tests freeze "now" at a UTC instant that falls on a *different* calendar
day in America/New_York, to prove day-of-week matching (and therefore
bedtime_enabled_today/bedtime_active) is computed against whichever `tz`
is passed in -- not always UTC.
"""
from datetime import datetime as real_datetime
from datetime import timezone

from app.familylink.api_client import FamilyLinkApiClient
from zoneinfo import ZoneInfo

# 2024-01-01 02:00 UTC == Monday. In America/New_York (UTC-5, no DST in
# winter) that's 2023-12-31 21:00 -- still Sunday evening.
_FROZEN_UTC_INSTANT = real_datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc)


class _FrozenDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _FROZEN_UTC_INSTANT.astimezone(tz)
        return _FROZEN_UTC_INSTANT


def _applied_time_limits_payload(day: int) -> list:
    """A minimal appliedTimeLimits response with one device that has an
    overnight bedtime rule (21:00-07:00) active on the given ISO weekday.
    """
    device_data: list = [None] * 26
    # A "CAEQ..."-prefixed 8-element rule entry is what the parser treats
    # as a bedtime window: [marker, day, state_flag(2=enabled), start, end, ...].
    device_data[5] = ["CAEQxxxx", day, 2, [21, 0], [7, 0], None, None, None]
    device_data[20] = "0"  # used_minutes
    device_data[25] = "device1"  # device_id fallback slot
    return [None, [device_data]]


def test_parse_applied_time_limits_matches_schedule_day_in_local_timezone(monkeypatch):
    monkeypatch.setattr("app.familylink.api_client.datetime", _FrozenDatetime)
    # Schedule day 7 (Sunday) matches America/New_York's local day at this
    # frozen instant, even though UTC's calendar day is already Monday.
    data = _applied_time_limits_payload(day=7)

    ny_result = FamilyLinkApiClient._parse_applied_time_limits(data, tz=ZoneInfo("America/New_York"))
    assert ny_result["bedtime_enabled_today"] is True
    assert ny_result["devices"]["device1"]["bedtime_active"] is True

    utc_result = FamilyLinkApiClient._parse_applied_time_limits(data, tz=timezone.utc)
    assert utc_result["bedtime_enabled_today"] is False
    assert utc_result["devices"]["device1"]["bedtime_active"] is False


def test_parse_applied_time_limits_defaults_to_utc_when_no_tz_given(monkeypatch):
    monkeypatch.setattr("app.familylink.api_client.datetime", _FrozenDatetime)
    # Schedule day 1 (Monday) matches UTC's calendar day at this instant --
    # confirms the backward-compatible default (tz=None -> UTC) still works.
    data = _applied_time_limits_payload(day=1)

    result = FamilyLinkApiClient._parse_applied_time_limits(data)
    assert result["bedtime_enabled_today"] is True
