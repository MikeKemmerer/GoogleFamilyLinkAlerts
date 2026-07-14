from app.diff.engine import diff_snapshots, flatten, is_ignored_path


def test_flatten_nested_dict():
    data = {"apps": {"com.tiktok": {"blocked": True, "limit_minutes": 30}}}
    flat = flatten(data)
    assert flat == {
        "apps.com.tiktok.blocked": True,
        "apps.com.tiktok.limit_minutes": 30,
    }


def test_flatten_list_indices():
    data = {"devices": [{"id": "abc"}, {"id": "def"}]}
    flat = flatten(data)
    assert flat["devices[0].id"] == "abc"
    assert flat["devices[1].id"] == "def"


def test_flatten_empty_containers():
    assert flatten({"apps": {}}) == {"apps": {}}
    assert flatten({"devices": []}) == {"devices": []}


def test_diff_first_snapshot_reports_everything_as_new():
    new = {"screen_time": {"limit_minutes": 60}}
    changes = diff_snapshots(None, new)
    assert len(changes) == 1
    assert changes[0].field_path == "screen_time.limit_minutes"
    assert changes[0].old_value is None
    assert changes[0].new_value == 60


def test_diff_detects_changed_value():
    old = {"screen_time": {"limit_minutes": 60}}
    new = {"screen_time": {"limit_minutes": 90}}
    changes = diff_snapshots(old, new)
    assert len(changes) == 1
    assert changes[0].field_path == "screen_time.limit_minutes"
    assert changes[0].old_value == 60
    assert changes[0].new_value == 90


def test_diff_detects_added_and_removed_fields():
    old = {"apps": {"a": {"blocked": True}}}
    new = {"apps": {"b": {"blocked": False}}}
    changes = diff_snapshots(old, new)
    paths = {c.field_path for c in changes}
    assert "apps.a.blocked" in paths
    assert "apps.b.blocked" in paths


def test_diff_no_changes_when_identical():
    data = {"a": 1, "b": {"c": 2}}
    assert diff_snapshots(data, data) == []


def test_is_ignored_path_matches_raw_time_limit_regardless_of_index():
    assert is_ignored_path("time_limit[1][0][1][0][0]")
    assert is_ignored_path("time_limit")
    assert not is_ignored_path("applied_time_limits.devices.dev1.remaining_minutes")


def test_is_ignored_path_matches_noisy_device_metadata():
    assert is_ignored_path("apps_and_usage.deviceInfo[0].displayInfo.thumbnail.imageUrl")
    assert is_ignored_path("apps_and_usage.deviceInfo[3].displayInfo.lastActivityTimeMillis")
    assert is_ignored_path("apps_and_usage.lastActivityRefreshTimestampMillis")
    assert is_ignored_path("apps_and_usage.deviceInfo[0].capabilityInfo.capabilities[9]")
    assert not is_ignored_path("apps_and_usage.deviceInfo[0].displayInfo.friendlyName")


def test_is_ignored_path_matches_app_usage_sessions_regardless_of_index():
    # appUsageSessions is a rolling window that reorders between polls --
    # diffing it by array index produces false-positive "changes" comparing
    # unrelated sessions. Confirmed in production to be ~92% of all noise.
    assert is_ignored_path("apps_and_usage.appUsageSessions[9].usage")
    assert is_ignored_path("apps_and_usage.appUsageSessions[97].appId.androidAppPackageName")
    assert is_ignored_path("apps_and_usage.appUsageSessions")
    assert not is_ignored_path("apps_and_usage.apps[3].title")


def test_is_ignored_path_matches_api_header_timestamp():
    # This is the API response's own timestamp, not app/device data -- it
    # differs on every single poll and would otherwise guarantee a "change"
    # every cycle forever.
    assert is_ignored_path("apps_and_usage.apiHeader.serverTimestampMillis")


def test_diff_ignores_raw_time_limit_and_noisy_metadata_by_default():
    old = {
        "time_limit": [[None, 1], [1, 2]],
        "apps_and_usage": {
            "deviceInfo": [{"displayInfo": {"thumbnail": {"imageUrl": "https://old"}, "friendlyName": "Chromebook"}}],
        },
        "applied_time_limits": {"devices": {"dev1": {"remaining_minutes": 60}}},
    }
    new = {
        "time_limit": [[None, 1], [1, 3]],
        "apps_and_usage": {
            "deviceInfo": [{"displayInfo": {"thumbnail": {"imageUrl": "https://new"}, "friendlyName": "Chromebook"}}],
        },
        "applied_time_limits": {"devices": {"dev1": {"remaining_minutes": 30}}},
    }
    changes = diff_snapshots(old, new)
    paths = {c.field_path for c in changes}
    assert paths == {"applied_time_limits.devices.dev1.remaining_minutes"}


def test_diff_can_disable_noise_filtering():
    old = {"time_limit": [1]}
    new = {"time_limit": [2]}
    assert diff_snapshots(old, new) == []
    changes = diff_snapshots(old, new, ignore_noisy_paths=False)
    assert len(changes) == 1
    assert changes[0].field_path == "time_limit[0]"
