"""Unit tests for app/familylink/app_usage.py (shared appUsageSessions helpers)."""
from app.familylink import app_usage


def test_usage_totals_by_app_and_date_sums_multiple_sessions():
    apps_and_usage = {
        "appUsageSessions": [
            {"usage": "60s", "appId": {"androidAppPackageName": "com.a"}, "date": {"year": 2026, "month": 7, "day": 20}},
            {"usage": "30s", "appId": {"androidAppPackageName": "com.a"}, "date": {"year": 2026, "month": 7, "day": 20}},
            {"usage": "10s", "appId": {"androidAppPackageName": "com.b"}, "date": {"year": 2026, "month": 7, "day": 20}},
            {"usage": "5s", "appId": {"androidAppPackageName": "com.a"}, "date": {"year": 2026, "month": 7, "day": 19}},
        ]
    }
    totals = app_usage.usage_totals_by_app_and_date(apps_and_usage)
    from datetime import date

    assert totals[("com.a", date(2026, 7, 20))] == 90.0
    assert totals[("com.b", date(2026, 7, 20))] == 10.0
    assert totals[("com.a", date(2026, 7, 19))] == 5.0


def test_hourly_usage_deltas_only_returns_positive_growth():
    from datetime import date

    old = {
        "appUsageSessions": [
            {"usage": "100s", "appId": {"androidAppPackageName": "com.a"}, "date": {"year": 2026, "month": 7, "day": 20}},
        ]
    }
    new = {
        "appUsageSessions": [
            # com.a grew by 20s.
            {"usage": "120s", "appId": {"androidAppPackageName": "com.a"}, "date": {"year": 2026, "month": 7, "day": 20}},
            # com.b is brand new -- the whole total counts as a delta.
            {"usage": "15s", "appId": {"androidAppPackageName": "com.b"}, "date": {"year": 2026, "month": 7, "day": 20}},
        ]
    }
    deltas = app_usage.hourly_usage_deltas(old, new)
    assert deltas == {
        ("com.a", date(2026, 7, 20)): 20.0,
        ("com.b", date(2026, 7, 20)): 15.0,
    }


def test_hourly_usage_deltas_drops_non_positive_changes():
    from datetime import date

    old = {
        "appUsageSessions": [
            {"usage": "100s", "appId": {"androidAppPackageName": "com.a"}, "date": {"year": 2026, "month": 7, "day": 20}},
        ]
    }
    new = {
        "appUsageSessions": [
            # Same or lower total -- should be dropped, not recorded as
            # negative/zero usage.
            {"usage": "100s", "appId": {"androidAppPackageName": "com.a"}, "date": {"year": 2026, "month": 7, "day": 20}},
        ]
    }
    assert app_usage.hourly_usage_deltas(old, new) == {}


def test_format_usage_duration_and_color_var():
    assert app_usage.format_usage_duration(65) == "1m"
    assert app_usage.format_usage_duration(3665) == "1h 1m"
    assert app_usage.app_usage_color_var("com.a") in app_usage.APP_USAGE_COLOR_VARS
