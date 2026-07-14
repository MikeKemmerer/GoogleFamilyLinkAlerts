from zoneinfo import ZoneInfo

from app.diff.labels import device_names_from_snapshot, humanize_field_path, humanize_value


def test_humanize_field_path_known_applied_time_limits_fields():
    device_names = {"dev1": "Chromebook"}
    assert humanize_field_path("applied_time_limits.bedtime_enabled_today") == "Bedtime enabled today"
    assert humanize_field_path(
        "applied_time_limits.devices.dev1.remaining_minutes", device_names
    ) == "Chromebook: screen time remaining"
    assert humanize_field_path(
        "applied_time_limits.devices.dev1.bedtime_window.start_ms", device_names
    ) == "Chromebook: bedtime starts"


def test_humanize_field_path_falls_back_to_shortened_device_id_when_unresolved():
    label = humanize_field_path("applied_time_limits.devices.unknowndevice1234.used_minutes")
    assert label.startswith("Device unknownd")
    assert label.endswith(": screen time used")


def test_humanize_field_path_generic_fallback_for_unknown_paths():
    label = humanize_field_path("apps_and_usage.apps[3].supervisionSetting.usageLimit.dailyUsageLimitMins")
    assert label == "Apps And Usage → Apps #3 → Supervision Setting → Usage Limit → Daily Usage Limit Mins"


def test_humanize_value_formats_none_bool_and_ms_timestamps():
    assert humanize_value("applied_time_limits.devices.dev1.bedtime_active", None) == "—"
    assert humanize_value("applied_time_limits.devices.dev1.bedtime_active", True) == "Yes"
    assert humanize_value("applied_time_limits.devices.dev1.bedtime_active", False) == "No"
    # 1783980000000 ms -> a real date/time, not a 13-digit number.
    rendered = humanize_value("applied_time_limits.devices.dev1.bedtime_window.start_ms", 1783980000000)
    assert rendered != "1783980000000"
    assert "-" in rendered and ":" in rendered


def test_humanize_value_passthrough_for_plain_values():
    assert humanize_value("applied_time_limits.devices.dev1.some_flag_count", 60) == "60"


def test_humanize_value_formats_minute_durations_as_hours_and_minutes():
    assert humanize_value("applied_time_limits.devices.dev1.used_minutes", 75) == "1h 15m"
    assert humanize_value("applied_time_limits.devices.dev1.remaining_minutes", 45) == "45m"
    assert humanize_value("applied_time_limits.devices.dev1.total_allowed_minutes", 120) == "2h"
    assert humanize_value("applied_time_limits.devices.dev1.remaining_minutes", 0) == "0m"
    assert humanize_value("apps_and_usage.apps[3].supervisionSetting.usageLimit.dailyUsageLimitMins", 90) == "1h 30m"


def test_humanize_value_renders_ms_timestamp_in_given_timezone_not_system_time():
    # 1700000000000 ms == 2023-11-14 22:13:20 UTC. Rendered in New York
    # (UTC-5 in November) it should show the *local* clock time, not the
    # UTC one -- this is what makes bedtime start/end times on the History
    # page match what's actually configured in Family Link.
    field = "applied_time_limits.devices.dev1.bedtime_window.start_ms"
    utc_rendered = humanize_value(field, 1700000000000, tz=ZoneInfo("UTC"))
    ny_rendered = humanize_value(field, 1700000000000, tz=ZoneInfo("America/New_York"))
    assert utc_rendered == "2023-11-14 22:13"
    assert ny_rendered == "2023-11-14 17:13"


def test_device_names_from_snapshot_builds_id_to_name_map():
    snapshot = {
        "apps_and_usage": {
            "deviceInfo": [
                {"deviceId": "dev1", "displayInfo": {"friendlyName": "Chromebook"}},
                {"deviceId": "dev2", "displayInfo": {}},
            ]
        }
    }
    assert device_names_from_snapshot(snapshot) == {"dev1": "Chromebook"}


def test_device_names_from_snapshot_handles_missing_data():
    assert device_names_from_snapshot(None) == {}
    assert device_names_from_snapshot({}) == {}
