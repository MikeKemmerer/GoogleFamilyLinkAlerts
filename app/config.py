"""Application configuration.

Loads infrastructure-level settings from environment variables (see
`.env.example`). Business-level configuration (children list, ntfy target,
poll interval) is configured via the web UI's first-run setup wizard and
persisted in the database -- see `app/db/models.py` (Settings table), not
here.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # familylink-auth container
    familylink_auth_base_url: str = "http://familylink-auth:8099"
    familylink_auth_api_key: str | None = None
    # Browser-facing URL for the familylink-auth noVNC login page (shown as a
    # link in the setup wizard / status page -- must be reachable from the
    # *user's browser*, not just container-to-container, so it defaults to
    # localhost + the published port rather than the docker-compose service name.
    familylink_auth_novnc_url: str = "http://localhost:6080"

    # Our app
    app_data_dir: Path = Path("/data")
    app_port: int = 8080

    @property
    def database_path(self) -> Path:
        return self.app_data_dir / "familylink_alerts.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"


settings = Settings()
