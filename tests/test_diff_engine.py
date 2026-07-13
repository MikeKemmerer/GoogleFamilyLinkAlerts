from app.diff.engine import diff_snapshots, flatten


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
