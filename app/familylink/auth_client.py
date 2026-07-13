"""Client for the upstream `familylink-auth` container.

That container (published by noiwid/HAFamilyLink, MIT licensed — see
third_party/NOTICE.md) runs a real Chromium browser via Playwright behind a
noVNC web view so a human can complete the actual Google login (including
2FA). Once logged in, it serves the resulting session cookies over a small
local HTTP API. We run it unmodified as a separate container and only talk
to it over the network -- see docker-compose.yml.

Contract (reverse-engineered from noiwid/HAFamilyLink's
`custom_components/familylink/auth/addon_client.py`):
  GET {base_url}/api/health           -> 200 if the service is reachable
  GET {base_url}/api/cookies          -> 200 {"cookies": [...]}   when logged in
                                         404                      when not yet logged in
                                         403                      when API key required/wrong
  Header: X-API-Key: <key>            (only required if API_KEY is configured
                                        on the familylink-auth container)
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .exceptions import AuthenticationError, NetworkError

_LOGGER = logging.getLogger(__name__)


class AuthClient:
    """Fetches Google session cookies from the familylink-auth container."""

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def health_ok(self) -> bool:
        """Return True if the familylink-auth container is reachable."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}/api/health")
                return resp.status_code == 200
        except httpx.HTTPError as err:
            _LOGGER.debug("familylink-auth health check failed: %s", err)
            return False

    async def get_cookies(self) -> list[dict[str, Any]] | None:
        """Fetch current session cookies.

        Returns:
            The list of cookie dicts if a valid session exists.
            None if no session has been established yet (HTTP 404).

        Raises:
            AuthenticationError: the auth container rejected the request
                because of a missing/incorrect API key (HTTP 403).
            NetworkError: the auth container could not be reached at all.
        """
        headers = {"X-API-Key": self._api_key} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}/api/cookies", headers=headers)
        except httpx.HTTPError as err:
            raise NetworkError(f"Could not reach familylink-auth at {self._base_url}: {err}") from err

        if resp.status_code == 200:
            cookies = resp.json().get("cookies", [])
            _LOGGER.debug("Loaded %d cookies from familylink-auth", len(cookies))
            return cookies
        if resp.status_code == 404:
            _LOGGER.info("familylink-auth has no session yet -- login required via noVNC")
            return None
        if resp.status_code == 403:
            raise AuthenticationError(
                "familylink-auth rejected the request (403): a matching API key is "
                "required. Set FAMILYLINK_AUTH_API_KEY the same on both containers."
            )
        raise NetworkError(f"Unexpected status {resp.status_code} from familylink-auth /api/cookies")
