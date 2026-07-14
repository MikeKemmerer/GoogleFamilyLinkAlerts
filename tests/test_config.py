import os
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("APP_DATA_DIR", str(Path(__file__).parent / "_tmp_data"))

from app.config import Settings  # noqa: E402


def test_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.app_port == 8080
    assert s.database_path == tmp_path / "familylink_alerts.db"
    assert s.database_url == f"sqlite:///{tmp_path / 'familylink_alerts.db'}"


def test_settings_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_PORT", "9090")
    monkeypatch.setenv("FAMILYLINK_AUTH_API_KEY", "secret123")
    s = Settings()
    assert s.app_port == 9090
    assert s.familylink_auth_api_key == "secret123"


def test_settings_timezone_defaults_to_utc():
    s = Settings(timezone="UTC")
    assert s.zone_info == ZoneInfo("UTC")


def test_settings_timezone_resolves_valid_iana_name():
    s = Settings(timezone="America/New_York")
    assert s.zone_info == ZoneInfo("America/New_York")


def test_settings_timezone_falls_back_to_utc_for_invalid_name():
    s = Settings(timezone="Not/A_Real_Zone")
    assert s.zone_info == ZoneInfo("UTC")
