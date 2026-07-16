"""Google Family Link API client (mostly read-only, monitoring-focused).

Adapted from `custom_components/familylink/client/api.py` in
noiwid/HAFamilyLink (MIT licensed — see third_party/NOTICE.md). That project
reverse-engineered these endpoints for a Home Assistant integration that
both reads *and* controls Family Link. This project only needs to *read*
settings in order to detect and alert on changes, so most control/mutation
endpoints (set bedtime, add time bonus, lock device, etc.) are intentionally
out of scope. The exceptions are `block_app`/`unblock_app`, used narrowly by
the "always blocked" app-enforcement feature (see app/poller.py) so a parent
can opt specific apps into "if this ever gets re-enabled, block it again
immediately", and `cancel_time_bonus`, used by the "auto-revoke bonus time"
child setting so a granted bonus gets revoked again on the next poll --
everything else remains read-only.

The `appliedTimeLimits` response is an undocumented, deeply nested
positional array (not a named JSON object), reverse-engineered by inspecting
real responses. That parsing logic is ported closely to the original because
re-deriving it from scratch isn't feasible without live captures -- see
GOOGLE_FAMILY_LINK_API_ANALYSIS.md in the upstream repo for their full notes.
If Google changes this response shape, only this file should need fixing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone, tzinfo
from typing import Any

import httpx

from .auth_client import AuthClient
from .exceptions import AuthenticationError, NetworkError, SessionExpiredError

_LOGGER = logging.getLogger(__name__)


class FamilyLinkApiClient:
    """Mostly-read-only client for Google's internal Family Link API.

    The only mutation methods are `block_app`/`unblock_app`, used solely by
    the opt-in "always blocked" app-enforcement feature. Everything else is
    read-only monitoring.
    """

    # Reverse-engineered endpoints (see NOTICE.md attribution above).
    BASE_URL = "https://kidsmanagement-pa.clients6.google.com/kidsmanagement/v1"
    ORIGIN = "https://familylink.google.com"
    API_KEY = "AIzaSyAQb1gupaJhY3CXQy2xmTwJMcjmot3M2hw"

    # SAPISIDHASH timestamps must stay fresh; rebuild headers periodically.
    SESSION_MAX_AGE = 1800  # seconds
    LOCATION_REFRESH_MODE_DO_NOT_REFRESH = "1"
    LOCATION_REFRESH_MODE_REFRESH = "2"

    def __init__(self, auth_client: AuthClient) -> None:
        self._auth_client = auth_client
        self._cookies: list[dict[str, Any]] | None = None
        self._cookie_header: str | None = None
        self._headers_created_at: float = 0
        self._account_id: str | None = None

    @staticmethod
    def _validate_id(value: str, name: str = "ID") -> str:
        if not value or not re.match(r"^[a-zA-Z0-9_\-]+$", value):
            raise ValueError(f"Invalid {name}: contains disallowed characters")
        return value

    def _people_url(self, account_id: str, suffix: str) -> str:
        self._validate_id(account_id, "account_id")
        return f"{self.BASE_URL}/people/{account_id}/{suffix}"

    async def authenticate(self) -> None:
        """Load fresh cookies from the familylink-auth container."""
        self._cookies = await self._auth_client.get_cookies()
        if not self._cookies:
            raise AuthenticationError(
                "No session cookies available yet. Complete the Google login via "
                "the familylink-auth container's noVNC page, then retry."
            )
        self._cookie_header = None  # force rebuild
        _LOGGER.info("Loaded %d cookies from familylink-auth", len(self._cookies))

    def is_authenticated(self) -> bool:
        return bool(self._cookies)

    @staticmethod
    def _generate_sapisidhash(sapisid: str, origin: str) -> str:
        """Build the SAPISIDHASH Authorization value Google's internal APIs expect."""
        timestamp = int(time.time())
        to_hash = f"{timestamp} {sapisid} {origin}"
        sha1_hash = hashlib.sha1(to_hash.encode("utf-8")).hexdigest()
        return f"{timestamp}_{sha1_hash}"

    def _get_cookie_header(self) -> str:
        """Build a raw `Cookie:` header string from the loaded cookie list.

        We build this manually (rather than relying on an HTTP client's
        cookie jar) because cookies from Playwright may include values with
        characters an auto-quoting cookie jar would mangle, and because the
        same cookie name can appear for multiple Google TLDs -- we prefer
        the plain `google.com` domain, same as upstream.
        """
        if self._cookie_header is not None:
            return self._cookie_header

        cookie_dict: dict[str, str] = {}
        cookie_domains: dict[str, str] = {}

        def domain_priority(domain: str) -> int:
            if domain == "google.com":
                return 0
            if domain.startswith("google.com.") or domain.startswith("google.co."):
                return 2
            return 1

        for cookie in self._cookies or []:
            name = cookie.get("name", "")
            value = cookie.get("value", "").strip('"')
            domain = cookie.get("domain", "").lower().lstrip(".")
            if not name or not value:
                continue
            if name in cookie_dict and domain_priority(domain) >= domain_priority(cookie_domains[name]):
                continue
            cookie_dict[name] = value
            cookie_domains[name] = domain

        self._cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
        return self._cookie_header

    def _get_sapisid(self) -> str:
        candidates = []
        for cookie in self._cookies or []:
            if cookie.get("name") != "SAPISID":
                continue
            domain = cookie.get("domain", "").lower().lstrip(".")
            if domain.startswith("google.") or ".google." in domain:
                candidates.append((domain, cookie.get("value", "").strip('"')))
        if not candidates:
            raise AuthenticationError("SAPISID cookie not found in authentication data")

        def priority(item: tuple[str, str]) -> int:
            domain = item[0]
            if domain == "google.com":
                return 0
            if domain.startswith("google.com.") or domain.startswith("google.co."):
                return 2
            return 1

        candidates.sort(key=priority)
        return candidates[0][1]

    def _auth_headers(self) -> dict[str, str]:
        if (
            self._headers_created_at
            and (time.time() - self._headers_created_at) <= self.SESSION_MAX_AGE
            and getattr(self, "_cached_headers", None)
        ):
            return self._cached_headers  # type: ignore[return-value]

        sapisid = self._get_sapisid()
        sapisidhash = self._generate_sapisidhash(sapisid, self.ORIGIN)
        self._cached_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Origin": self.ORIGIN,
            "X-Goog-Api-Key": self.API_KEY,
            "Authorization": f"SAPISIDHASH {sapisidhash}",
        }
        self._headers_created_at = time.time()
        return self._cached_headers

    async def _get(self, url: str, params: list[tuple[str, str]] | None = None,
                    content_type: str = "application/json") -> Any:
        if not self.is_authenticated():
            raise AuthenticationError("Not authenticated")
        headers = {
            **self._auth_headers(),
            "Content-Type": content_type,
            "Cookie": self._get_cookie_header(),
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as err:
            raise NetworkError(f"Request to {url} failed: {err}") from err

        if resp.status_code == 401:
            raise SessionExpiredError("Session expired, please re-authenticate")
        if resp.status_code != 200:
            raise NetworkError(f"GET {url} returned HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def _post(self, url: str, payload: Any,
                     content_type: str = "application/json+protobuf") -> Any:
        if not self.is_authenticated():
            raise AuthenticationError("Not authenticated")
        headers = {
            **self._auth_headers(),
            "Content-Type": content_type,
            "Cookie": self._get_cookie_header(),
        }
        # Send the pre-serialized body ourselves (rather than httpx's `json=`
        # kwarg) so we control the exact bytes/content-type sent -- these
        # endpoints use `application/json+protobuf`, matching the read
        # endpoints above, not plain `application/json`.
        body = json.dumps(payload).encode("utf-8")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, content=body, headers=headers)
        except httpx.HTTPError as err:
            raise NetworkError(f"Request to {url} failed: {err}") from err

        if resp.status_code == 401:
            raise SessionExpiredError("Session expired, please re-authenticate")
        if resp.status_code != 200:
            raise NetworkError(f"POST {url} returned HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else None

    # -- Discovery -----------------------------------------------------

    async def get_family_members(self) -> dict[str, Any]:
        """Return the raw family-members payload (parents + children)."""
        return await self._get(f"{self.BASE_URL}/families/mine/members")

    async def get_all_supervised_children(self) -> list[dict[str, str]]:
        """Auto-discover supervised children -- used by the setup wizard."""
        data = await self.get_family_members()
        children = []
        for member in data.get("members", []):
            info = member.get("memberSupervisionInfo")
            if info and info.get("isSupervisedMember"):
                profile = member.get("profile", {})
                children.append({
                    "id": member["userId"],
                    "name": profile.get("displayName", "Unknown"),
                    "avatar_url": profile.get("profileImageUrl"),
                })
        if not children:
            raise ValueError("No supervised children found in this Family Link family")
        return children

    # -- Settings snapshots ---------------------------------------------

    async def get_apps_and_usage(self, account_id: str) -> dict[str, Any]:
        """Installed apps, per-app limits/blocking, and devices for a child."""
        params = [
            ("capabilities", "CAPABILITY_APP_USAGE_SESSION"),
            ("capabilities", "CAPABILITY_SUPERVISION_CAPABILITIES"),
        ]
        return await self._get(self._people_url(account_id, "appsandusage"), params=params)

    @staticmethod
    def _parse_location_response(data: Any) -> dict[str, Any] | None:
        if not isinstance(data, list) or len(data) < 2:
            return None

        child_data = data[1]
        if not isinstance(child_data, list) or len(child_data) < 3:
            return None

        location_data = child_data[2]
        if not isinstance(location_data, list) or len(location_data) < 2:
            return None

        coords = location_data[0]
        if not isinstance(coords, list) or len(coords) < 2:
            return None

        try:
            latitude = float(coords[0])
            longitude = float(coords[1])
        except (TypeError, ValueError):
            return None

        accuracy = None
        if len(location_data) > 2 and location_data[2] not in (None, ""):
            try:
                accuracy = int(location_data[2])
            except (TypeError, ValueError):
                accuracy = None

        timestamp = None
        timestamp_iso = None
        if len(location_data) > 1 and location_data[1] not in (None, ""):
            try:
                timestamp = int(location_data[1])
                timestamp_iso = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                timestamp = None
                timestamp_iso = None

        place_name = None
        place_address = None
        place_info = location_data[4] if len(location_data) > 4 else None
        if isinstance(place_info, list):
            if len(place_info) > 1 and isinstance(place_info[1], str):
                place_name = place_info[1]
            if len(place_info) > 2 and isinstance(place_info[2], str):
                place_address = place_info[2]
        if place_address is None and len(location_data) > 5 and isinstance(location_data[5], str):
            place_address = location_data[5]

        source_device_id = location_data[6] if len(location_data) > 6 and isinstance(location_data[6], str) else None

        battery_level = None
        if len(location_data) > 8 and isinstance(location_data[8], list) and location_data[8]:
            try:
                battery_level = int(location_data[8][0])
            except (TypeError, ValueError):
                battery_level = None

        return {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy,
            "timestamp": timestamp_iso,
            "source_device_id": source_device_id,
            "place_name": place_name,
            "place_address": place_address,
            "battery_level": battery_level,
        }

    async def get_location(self, account_id: str, refresh: bool = False) -> dict[str, Any] | None:
        """Get a child's GPS location and source-device battery level.

        Ported from noiwid/HAFamilyLink's `async_get_location` (MIT licensed
        -- see third_party/NOTICE.md). Google's protobuf-like response is a
        positional array: coordinates at `[2][0]`, timestamp at `[2][1]`,
        accuracy at `[2][2]`, optional place info at `[2][4]`, address at
        `[2][5]`, source device ID at `[2][6]`, and battery at `[2][8][0]`.
        """
        self._validate_id(account_id, "account_id")
        if not self.is_authenticated():
            raise AuthenticationError("Not authenticated")

        headers = {
            **self._auth_headers(),
            "Content-Type": "application/json+protobuf",
            "Cookie": self._get_cookie_header(),
        }
        params = [
            (
                "locationRefreshMode",
                self.LOCATION_REFRESH_MODE_REFRESH if refresh else self.LOCATION_REFRESH_MODE_DO_NOT_REFRESH,
            ),
            ("supportedConsents", "SUPERVISED_LOCATION_SHARING"),
        ]
        url = f"{self.BASE_URL}/families/mine/location/{account_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as err:
            raise NetworkError(f"Request to {url} failed: {err}") from err

        if resp.status_code == 401:
            raise SessionExpiredError("Session expired, please re-authenticate")
        if resp.status_code == 404:
            _LOGGER.info("No location available for child %s", account_id)
            return None
        if resp.status_code != 200:
            _LOGGER.warning("GET %s returned HTTP %s: %s", url, resp.status_code, resp.text[:500])
            return None

        try:
            parsed = self._parse_location_response(resp.json())
        except ValueError:
            _LOGGER.warning("Location response for %s was not valid JSON", account_id)
            return None

        if not parsed:
            _LOGGER.warning("Location response for %s had an unexpected shape", account_id)
            return None

        source_device_name = None
        source_device_id = parsed.pop("source_device_id", None)
        if source_device_id:
            try:
                apps_and_usage = await self.get_apps_and_usage(account_id)
                for device in apps_and_usage.get("deviceInfo", []):
                    if device.get("deviceId") != source_device_id:
                        continue
                    source_device_name = device.get("displayInfo", {}).get("friendlyName")
                    break
            except (AuthenticationError, NetworkError, SessionExpiredError, ValueError):
                _LOGGER.debug("Could not resolve source device name for %s", source_device_id, exc_info=True)

        return {
            **parsed,
            "source_device_name": source_device_name,
        }

    @staticmethod
    def get_app_package_name(app: dict[str, Any]) -> str | None:
        """Extract an app's package name from a `apps_and_usage.apps[N]` entry.

        Confirmed against live production data (2026-07-14): each entry has
        a flat `packageName` string field, e.g.
        `apps_and_usage.apps[3].packageName == "com.google.android.youtube"`.
        Also falls back to the sibling `appId.androidAppPackageName` shape
        used by `appUsageSessions[*]` (see app/diff/engine.py's ignore
        patterns), in case Google returns that shape here for some accounts.
        """
        package_name = app.get("packageName")
        if package_name:
            return package_name
        app_id = app.get("appId")
        if isinstance(app_id, dict):
            return app_id.get("androidAppPackageName")
        return None

    async def block_app(self, account_id: str, package_name: str) -> None:
        """Block a specific app for a child (opt-in "always blocked" enforcement only).

        Ported from noiwid/HAFamilyLink's `async_block_app` (MIT licensed --
        see third_party/NOTICE.md). Payload shape:
        `[account_id, [[[package_name], [1]]]]` where the trailing `[1]` is
        the "hidden"/block flag.
        """
        self._validate_id(account_id, "account_id")
        payload = [account_id, [[[package_name], [1]]]]
        await self._post(self._people_url(account_id, "apps:updateRestrictions"), payload)

    async def unblock_app(self, account_id: str, package_name: str) -> None:
        """Remove a block placed by `block_app` (empty array clears the restriction)."""
        self._validate_id(account_id, "account_id")
        payload = [account_id, [[[package_name], []]]]
        await self._post(self._people_url(account_id, "apps:updateRestrictions"), payload)

    async def cancel_time_bonus(self, account_id: str, override_id: str) -> None:
        """Cancel/revoke an active time-bonus override (extra screen time a
        parent granted via the Family Link app), used by the "auto-revoke
        bonus time" child setting (see app/poller.py).

        Ported from noiwid/HAFamilyLink's `async_cancel_time_bonus` (MIT
        licensed -- see third_party/NOTICE.md). Google uses a DELETE-via-POST
        convention here (`?$httpMethod=DELETE` query param on a POST, no
        real HTTP DELETE verb and no request body) rather than the
        `apps:updateRestrictions`/`timeLimitOverrides:batchCreate` POST body
        shape used by `block_app`/`unblock_app` above.
        """
        self._validate_id(account_id, "account_id")
        self._validate_id(override_id, "override_id")
        if not self.is_authenticated():
            raise AuthenticationError("Not authenticated")
        url = f"{self._people_url(account_id, f'timeLimitOverride/{override_id}')}?$httpMethod=DELETE"
        headers = {
            **self._auth_headers(),
            "Content-Type": "application/json+protobuf",
            "Cookie": self._get_cookie_header(),
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers)
        except httpx.HTTPError as err:
            raise NetworkError(f"Request to {url} failed: {err}") from err
        if resp.status_code == 401:
            raise SessionExpiredError("Session expired, please re-authenticate")
        if resp.status_code != 200:
            raise NetworkError(f"POST {url} returned HTTP {resp.status_code}: {resp.text[:500]}")

    async def get_time_limit(self, account_id: str) -> dict[str, Any]:
        """Bedtime/school-time rule configuration (schedules, enabled flags)."""
        params = [
            ("capabilities", "TIME_LIMIT_CLIENT_CAPABILITY_SCHOOLTIME"),
            ("timeLimitKey.type", "SUPERVISED_DEVICES"),
        ]
        try:
            return await self._get(
                self._people_url(account_id, "timeLimit"),
                params=params,
                content_type="application/json+protobuf",
            )
        except NetworkError:
            _LOGGER.warning("Failed to fetch time limit rules for %s", account_id)
            return {}

    async def get_applied_time_limits(self, account_id: str, tz: tzinfo | None = None) -> dict[str, Any]:
        """Per-device applied state: remaining time, bedtime/school windows, lock state.

        Parses Google's undocumented positional-array response. See the
        module docstring for why this mirrors upstream's parsing closely.

        `tz` should be the family's configured local IANA timezone (see
        `app.db.settings_store.get_zone_info`) -- Google's schedule
        weekday/hour values are local, not UTC, so using UTC here (the
        default, for backward compatibility) will compute the wrong
        "today"/"active right now" during the family's evening hours once
        UTC's calendar date has rolled over ahead of local time.
        """
        params = [("capabilities", "TIME_LIMIT_CLIENT_CAPABILITY_SCHOOLTIME")]
        data = await self._get(
            self._people_url(account_id, "appliedTimeLimits"),
            params=params,
            content_type="application/json+protobuf",
        )
        return self._parse_applied_time_limits(data, tz=tz)

    @staticmethod
    def _parse_applied_time_limits(data: Any, tz: tzinfo | None = None) -> dict[str, Any]:
        tz = tz or timezone.utc
        device_lock_states: dict[str, bool] = {}
        devices: dict[str, dict[str, Any]] = {}
        bedtime_enabled_today = False
        schooltime_enabled_today = False

        if not (isinstance(data, list) and len(data) > 1 and isinstance(data[1], list)):
            return {
                "device_lock_states": device_lock_states,
                "devices": devices,
                "bedtime_enabled_today": bedtime_enabled_today,
                "schooltime_enabled_today": schooltime_enabled_today,
            }

        current_day = datetime.now(tz).isoweekday()

        for device_data in data[1]:
            if not isinstance(device_data, list) or len(device_data) < 25:
                continue

            device_id = None
            if device_data[0] and isinstance(device_data[0], list) and len(device_data[0]) > 3:
                device_id = device_data[0][3]
            elif len(device_data) > 25 and device_data[25]:
                device_id = device_data[25]
            if not device_id:
                continue

            has_lock_override = device_data[0] is not None and isinstance(device_data[0], list)
            is_locked = bool(has_lock_override and len(device_data[0]) > 2 and device_data[0][2] == 1)
            device_lock_states[device_id] = is_locked

            device_info: dict[str, Any] = {
                "total_allowed_minutes": 0,
                "used_minutes": 0,
                "remaining_minutes": 0,
                "daily_limit_enabled": False,
                "daily_limit_minutes": 0,
                "bedtime_window": None,
                "schooltime_window": None,
                "bedtime_active": False,
                "schooltime_active": False,
                "bonus_minutes": 0,
                "bonus_override_id": None,
            }

            # Bonus override lives in device_data[0] when type == 10. Its
            # override_id (device_data[0][0]) is needed to later cancel/
            # revoke the bonus via `cancel_time_bonus` (auto-revoke feature).
            override = device_data[0]
            if override and isinstance(override, list) and len(override) > 13 and override[2] == 10:
                try:
                    bonus_seconds_str = override[13][0][0]
                    if isinstance(bonus_seconds_str, str) and bonus_seconds_str.isdigit():
                        device_info["bonus_minutes"] = int(bonus_seconds_str) // 60
                        device_info["bonus_override_id"] = override[0]
                except (IndexError, TypeError):
                    pass

            if len(device_data) > 20 and isinstance(device_data[20], str) and device_data[20].isdigit():
                device_info["used_minutes"] = int(device_data[20]) // 60000

            for idx, item in enumerate(device_data):
                if isinstance(item, list) and len(item) >= 4 and isinstance(item[0], str):
                    first_elem = item[0]
                    is_caeq = first_elem.startswith("CAEQ")
                    is_camq = first_elem.startswith("CAMQ")
                    is_uuid = len(first_elem) == 36 and first_elem.count("-") == 4
                    if not (is_caeq or is_camq or is_uuid):
                        continue

                    if len(item) == 6:
                        day, state_flag, minutes = item[1], item[2], item[3]
                        if (
                            isinstance(day, int) and day == current_day
                            and isinstance(state_flag, int) and isinstance(minutes, int)
                        ):
                            device_info["daily_limit_enabled"] = (idx < 10) and (state_flag == 2)
                            device_info["daily_limit_minutes"] = minutes

                    elif len(item) == 8:
                        day, state_flag = item[1], item[2]
                        start_time, end_time = item[3], item[4]
                        parse_as_bedtime = is_caeq or (is_uuid and device_info["bedtime_window"] is None)
                        if (
                            isinstance(day, int) and day == current_day
                            and isinstance(state_flag, int) and state_flag == 2
                            and isinstance(start_time, list) and len(start_time) == 2
                            and isinstance(end_time, list) and len(end_time) == 2
                        ):
                            now = datetime.now(tz)
                            start_dt = now.replace(hour=start_time[0], minute=start_time[1], second=0, microsecond=0)
                            end_dt = now.replace(hour=end_time[0], minute=end_time[1], second=0, microsecond=0)
                            if end_time[0] < start_time[0] or (end_time[0] == start_time[0] and end_time[1] < start_time[1]):
                                active = (now >= start_dt) or (now < end_dt)
                            else:
                                active = start_dt <= now < end_dt
                            window = {"start_ms": int(start_dt.timestamp() * 1000), "end_ms": int(end_dt.timestamp() * 1000)}
                            if parse_as_bedtime:
                                device_info["bedtime_window"] = window
                                device_info["bedtime_active"] = active
                                bedtime_enabled_today = True
                            else:
                                device_info["schooltime_window"] = window
                                device_info["schooltime_active"] = active
                                schooltime_enabled_today = True

            if device_info["daily_limit_enabled"] and device_info["daily_limit_minutes"] > 0:
                limit = device_info["daily_limit_minutes"]
                used = device_info["used_minutes"]
                bonus = device_info["bonus_minutes"]
                device_info["daily_limit_remaining"] = max(0, limit - used)
                if bonus > 0:
                    device_info["total_allowed_minutes"] = bonus
                    device_info["remaining_minutes"] = bonus
                else:
                    device_info["total_allowed_minutes"] = limit
                    device_info["remaining_minutes"] = max(0, limit - used)

            devices[device_id] = device_info

        return {
            "device_lock_states": device_lock_states,
            "devices": devices,
            "bedtime_enabled_today": bedtime_enabled_today,
            "schooltime_enabled_today": schooltime_enabled_today,
        }
