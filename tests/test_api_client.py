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

import pytest

from app.familylink.api_client import FamilyLinkApiClient
from app.familylink.exceptions import AuthenticationError, NetworkError
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


def _bonus_override_payload(granted_by_id, *, override_id: str = "override1", bonus_seconds: str = "600") -> list:
    device_data: list = [None] * 26
    override: list = [None] * 14
    override[0] = override_id
    override[2] = 10
    override[3] = "device1"
    override[8] = granted_by_id
    override[13] = [[bonus_seconds]]
    device_data[0] = override
    device_data[20] = "0"
    device_data[25] = "device1"
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


def test_parse_applied_time_limits_extracts_bonus_granted_by_id():
    data = _bonus_override_payload("parent-profile-1")

    result = FamilyLinkApiClient._parse_applied_time_limits(data)

    device = result["devices"]["device1"]
    assert device["bonus_minutes"] == 10
    assert device["bonus_override_id"] == "override1"
    assert device["bonus_granted_by_id"] == "parent-profile-1"
    assert device["bonus_granted_by"] is None


@pytest.mark.parametrize("granted_by_id", [None, "", "device1", "override1", ["not-a-string"]])
def test_parse_applied_time_limits_ignores_implausible_bonus_granted_by_id(granted_by_id):
    data = _bonus_override_payload(granted_by_id)

    result = FamilyLinkApiClient._parse_applied_time_limits(data)

    assert result["devices"]["device1"]["bonus_granted_by_id"] is None


def test_get_app_package_name_prefers_flat_packageName_field():
    # Confirmed against live data: apps_and_usage.apps[N] uses a flat
    # `packageName` string, not an `appId` wrapper.
    app = {"packageName": "com.tiktok.android", "appId": {"androidAppPackageName": "ignored"}}
    assert FamilyLinkApiClient.get_app_package_name(app) == "com.tiktok.android"


def test_get_app_package_name_falls_back_to_app_id_wrapper():
    app = {"appId": {"androidAppPackageName": "com.tiktok.android"}}
    assert FamilyLinkApiClient.get_app_package_name(app) == "com.tiktok.android"


def test_get_app_package_name_returns_none_when_absent():
    assert FamilyLinkApiClient.get_app_package_name({}) is None


async def test_block_app_posts_expected_restriction_payload(monkeypatch):
    client = FamilyLinkApiClient(auth_client=None)
    calls = []

    async def fake_post(url, payload, **kwargs):
        calls.append((url, payload))

    monkeypatch.setattr(client, "_post", fake_post)
    await client.block_app("child1", "com.tiktok.android")

    assert len(calls) == 1
    url, payload = calls[0]
    assert url == f"{FamilyLinkApiClient.BASE_URL}/people/child1/apps:updateRestrictions"
    assert payload == ["child1", [[["com.tiktok.android"], [1]]]]


async def test_unblock_app_posts_empty_restriction_payload(monkeypatch):
    client = FamilyLinkApiClient(auth_client=None)
    calls = []

    async def fake_post(url, payload, **kwargs):
        calls.append((url, payload))

    monkeypatch.setattr(client, "_post", fake_post)
    await client.unblock_app("child1", "com.tiktok.android")

    assert len(calls) == 1
    url, payload = calls[0]
    assert url == f"{FamilyLinkApiClient.BASE_URL}/people/child1/apps:updateRestrictions"
    assert payload == ["child1", [[["com.tiktok.android"], []]]]


class _FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None, content=b"{}"):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self.content = content

    def json(self):
        if self._json_data is None:
            raise ValueError("No JSON data configured")
        return self._json_data


class _FakeHttpxClient:
    """Minimal stand-in for httpx.AsyncClient's async context-manager +
    .post() used directly by cancel_time_bonus (it can't reuse client._post,
    which always JSON-serializes a body -- see api_client.py docstring)."""

    calls: list = []
    response = _FakeResponse()

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, **kwargs):
        type(self).calls.append((url, headers))
        return type(self).response

    async def get(self, url, params=None, headers=None, **kwargs):
        type(self).calls.append((url, params, headers))
        return type(self).response


async def test_cancel_time_bonus_posts_delete_via_post_with_no_body(monkeypatch):
    import httpx as httpx_module

    client = FamilyLinkApiClient(auth_client=None)
    client._cookies = [
        {"name": "SID", "value": "abc", "domain": ".google.com"},
        {"name": "SAPISID", "value": "def", "domain": ".google.com"},
    ]
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.response = _FakeResponse(status_code=200)
    monkeypatch.setattr(httpx_module, "AsyncClient", _FakeHttpxClient)

    await client.cancel_time_bonus("child1", "override1")

    assert len(_FakeHttpxClient.calls) == 1
    url, headers = _FakeHttpxClient.calls[0]
    assert url == (
        f"{FamilyLinkApiClient.BASE_URL}/people/child1/timeLimitOverride/override1?$httpMethod=DELETE"
    )
    assert "Cookie" in headers


async def test_cancel_time_bonus_raises_when_not_authenticated():
    client = FamilyLinkApiClient(auth_client=None)
    with pytest.raises(AuthenticationError):
        await client.cancel_time_bonus("child1", "override1")


async def test_cancel_time_bonus_raises_network_error_on_non_200(monkeypatch):
    import httpx as httpx_module

    client = FamilyLinkApiClient(auth_client=None)
    client._cookies = [
        {"name": "SID", "value": "abc", "domain": ".google.com"},
        {"name": "SAPISID", "value": "def", "domain": ".google.com"},
    ]
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.response = _FakeResponse(status_code=500, text="boom")
    monkeypatch.setattr(httpx_module, "AsyncClient", _FakeHttpxClient)

    with pytest.raises(NetworkError):
        await client.cancel_time_bonus("child1", "override1")


async def test_get_location_parses_upstream_array_response_and_resolves_device_name(monkeypatch):
    import httpx as httpx_module

    client = FamilyLinkApiClient(auth_client=None)
    client._cookies = [
        {"name": "SID", "value": "abc", "domain": ".google.com"},
        {"name": "SAPISID", "value": "def", "domain": ".google.com"},
    ]
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.response = _FakeResponse(
        status_code=200,
        json_data=[
            [None, "1721145600000"],
            [
                "child1",
                1,
                [
                    [47.6062, -122.3321],
                    "1721145600000",
                    "25",
                    "300000",
                    ["place123", "Home", "123 Main St, Seattle, WA"],
                    "123 Main St, Seattle, WA",
                    "device-1",
                    None,
                    [87, 1],
                ],
                None,
                1,
            ],
        ],
    )
    monkeypatch.setattr(httpx_module, "AsyncClient", _FakeHttpxClient)

    async def fake_get_apps_and_usage(account_id):
        assert account_id == "child1"
        return {
            "deviceInfo": [
                {"deviceId": "device-1", "displayInfo": {"friendlyName": "Pixel 8"}},
            ]
        }

    monkeypatch.setattr(client, "get_apps_and_usage", fake_get_apps_and_usage)

    result = await client.get_location("child1")

    assert _FakeHttpxClient.calls[0][0] == f"{FamilyLinkApiClient.BASE_URL}/families/mine/location/child1"
    assert _FakeHttpxClient.calls[0][1] == [
        ("locationRefreshMode", FamilyLinkApiClient.LOCATION_REFRESH_MODE_DO_NOT_REFRESH),
        ("supportedConsents", "SUPERVISED_LOCATION_SHARING"),
    ]
    assert result == {
        "latitude": 47.6062,
        "longitude": -122.3321,
        "accuracy": 25,
        "timestamp": "2024-07-16T16:00:00+00:00",
        "source_device_name": "Pixel 8",
        "place_name": "Home",
        "place_address": "123 Main St, Seattle, WA",
        "battery_level": 87,
    }


async def test_get_location_returns_none_for_malformed_array_response(monkeypatch):
    import httpx as httpx_module

    client = FamilyLinkApiClient(auth_client=None)
    client._cookies = [
        {"name": "SID", "value": "abc", "domain": ".google.com"},
        {"name": "SAPISID", "value": "def", "domain": ".google.com"},
    ]
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.response = _FakeResponse(
        status_code=200,
        json_data=[[None, "1721145600000"], ["child1", 1, [[47.6062], "1721145600000"]]],
    )
    monkeypatch.setattr(httpx_module, "AsyncClient", _FakeHttpxClient)

    assert await client.get_location("child1") is None


async def test_get_location_returns_none_for_404(monkeypatch):
    import httpx as httpx_module

    client = FamilyLinkApiClient(auth_client=None)
    client._cookies = [
        {"name": "SID", "value": "abc", "domain": ".google.com"},
        {"name": "SAPISID", "value": "def", "domain": ".google.com"},
    ]
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.response = _FakeResponse(status_code=404, text="not found", json_data=None, content=b"")
    monkeypatch.setattr(httpx_module, "AsyncClient", _FakeHttpxClient)

    assert await client.get_location("child1") is None
