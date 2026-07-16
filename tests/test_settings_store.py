from sqlmodel import Session, SQLModel, create_engine

from app.db import settings_store


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def test_get_timezone_defaults_to_env_setting_when_unset(tmp_path, monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "timezone", "America/Chicago")
    engine = _engine(tmp_path)
    with Session(engine) as s:
        assert settings_store.get_timezone(s) == "America/Chicago"


def test_set_timezone_overrides_env_default(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as s:
        settings_store.set_timezone(s, "Europe/London")
        assert settings_store.get_timezone(s) == "Europe/London"


def test_is_valid_timezone():
    assert settings_store.is_valid_timezone("America/New_York") is True
    assert settings_store.is_valid_timezone("Not/ARealZone") is False


def test_get_zone_info_falls_back_to_utc_for_invalid_saved_value(tmp_path):
    from zoneinfo import ZoneInfo
    engine = _engine(tmp_path)
    with Session(engine) as s:
        # Bypass validation to simulate a bad/legacy value already in the DB.
        settings_store.set_(s, settings_store._KEY_TIMEZONE, "Not/ARealZone")
        assert settings_store.get_zone_info(s) == ZoneInfo("UTC")


def test_get_zone_info_resolves_valid_saved_value(tmp_path):
    from zoneinfo import ZoneInfo
    engine = _engine(tmp_path)
    with Session(engine) as s:
        settings_store.set_timezone(s, "Asia/Tokyo")
        assert settings_store.get_zone_info(s) == ZoneInfo("Asia/Tokyo")


def test_location_tracking_enabled_defaults_false_and_can_be_enabled(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as s:
        assert settings_store.get_location_tracking_enabled(s) is False
        settings_store.set_location_tracking_enabled(s, True)
        assert settings_store.get_location_tracking_enabled(s) is True
