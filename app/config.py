"""Application configuration.

Loads infrastructure-level settings from environment variables (see
`.env.example`). Business-level configuration (children list, ntfy target,
poll interval) is configured via the web UI's first-run setup wizard and
persisted in the database -- see `app/db/models.py` (Settings table), not
here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_settings import BaseSettings, SettingsConfigDict

_LOGGER = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # familylink-auth container
    familylink_auth_base_url: str = "http://familylink-auth:8099"
    familylink_auth_api_key: str | None = None
    # Browser-facing URLs for familylink-auth's own web UI (where the
    # "Start Authentication" button lives) and its noVNC login screen -- both
    # must be reachable from the *user's browser*, not just
    # container-to-container, so they default to localhost + the published
    # port rather than the docker-compose service name.
    familylink_auth_ui_url: str = "http://localhost:8099"
    familylink_auth_novnc_url: str = "http://localhost:6080"

    @property
    def familylink_auth_ui_url_with_key(self) -> str:
        """familylink_auth_ui_url with ?api_key=... appended, if configured.

        familylink-auth's own web UI enforces the API key on every request
        (not just /api/cookies) when API_KEY is set, so a bare link to it
        would 403 on "Start Authentication".
        """
        if not self.familylink_auth_api_key:
            return self.familylink_auth_ui_url
        sep = "&" if "?" in self.familylink_auth_ui_url else "?"
        return f"{self.familylink_auth_ui_url}{sep}api_key={self.familylink_auth_api_key}"

    # Our app
    app_data_dir: Path = Path("/data")
    app_port: int = 8080
    # Family's local IANA timezone (e.g. "America/New_York"). Google Family
    # Link's bedtime/school-time schedules are configured by weekday and
    # time-of-day in the family's local time, not UTC -- so this must match
    # the same TIMEZONE the familylink-auth container uses (see
    # docker-compose.yml), otherwise "today"/"active right now" can be
    # computed against the wrong day, especially in the evening when UTC's
    # calendar date has already rolled over but it isn't midnight locally
    # yet. Defaults to UTC (safe but only correct for UTC-timezone families)
    # if unset or invalid, rather than failing to start.
    timezone: str = "UTC"

    @property
    def database_path(self) -> Path:
        return self.app_data_dir / "familylink_alerts.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    @property
    def zone_info(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            _LOGGER.warning("Invalid TIMEZONE '%s', falling back to UTC", self.timezone)
            return ZoneInfo("UTC")


settings = Settings()
