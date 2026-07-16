from app.notify.categories import CATEGORIES, DEFAULT_ENABLED_CATEGORIES, category_for_field_path


def test_default_enabled_categories_covers_all_known_categories():
    assert DEFAULT_ENABLED_CATEGORIES == frozenset(CATEGORIES)


def test_category_for_field_path_app_blocking():
    assert category_for_field_path(
        "apps_and_usage.apps[com.tiktok.android].supervisionSetting.hidden"
    ) == "app_blocking"


def test_category_for_field_path_screen_time():
    for suffix in ("total_allowed_minutes", "used_minutes", "remaining_minutes", "daily_limit_enabled", "daily_limit_minutes"):
        assert category_for_field_path(f"applied_time_limits.devices.dev1.{suffix}") == "screen_time"


def test_category_for_field_path_bonus_time():
    for suffix in ("bonus_minutes", "bonus_override_id"):
        assert category_for_field_path(f"applied_time_limits.devices.dev1.{suffix}") == "bonus_time"


def test_category_for_field_path_bedtime_schooltime():
    assert category_for_field_path("applied_time_limits.bedtime_enabled_today") == "bedtime_schooltime"
    assert category_for_field_path("applied_time_limits.schooltime_enabled_today") == "bedtime_schooltime"
    assert category_for_field_path(
        "applied_time_limits.devices.dev1.bedtime_window.start_ms"
    ) == "bedtime_schooltime"
    assert category_for_field_path("applied_time_limits.devices.dev1.schooltime_active") == "bedtime_schooltime"


def test_category_for_field_path_location():
    assert category_for_field_path("location.latitude") == "location"
    assert category_for_field_path("location.source_device_name") == "location"


def test_category_for_field_path_device_lock():
    assert category_for_field_path("applied_time_limits.device_lock_states.dev1") == "device_lock"


def test_category_for_field_path_falls_back_to_other():
    assert category_for_field_path("website_filter.blocked_sites[0]") == "other"
