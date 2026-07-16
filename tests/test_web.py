"""Web UI tests: first-run setup wizard, settings page, history page.

Builds a minimal FastAPI app from the same routers as app.main but skips the
lifespan (migrations + scheduler) so tests don't touch the real global
settings singleton or spin up a background poller. Auth/API clients are
faked via monkeypatching the names each router module imported directly.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.db.models import Child, ChangeEvent, LatestSnapshot, PollFailure
from app.web import auth, history, settings as settings_web, setup, status
from app.web.deps import get_db

_FROZEN_STATUS_NOW = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)


class _FrozenStatusDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _FROZEN_STATUS_NOW.astimezone(tz)
        return _FROZEN_STATUS_NOW


def _status_snapshot_with_location():
    return {
        "apps_and_usage": {
            "deviceInfo": [
                {"deviceId": "dev1", "displayInfo": {"friendlyName": "Pixel 8"}},
            ]
        },
        "applied_time_limits": {
            "devices": {
                "dev1": {
                    "used_minutes": 45,
                    "daily_limit_enabled": True,
                    "remaining_minutes": 75,
                    "daily_limit_minutes": 120,
                }
            },
            "device_lock_states": {"dev1": False},
        },
        "location": {
            "latitude": 47.6062,
            "longitude": -122.3321,
            "accuracy": 25,
            "timestamp": "2024-07-16T16:00:00+00:00",
            "place_name": "Home",
            "place_address": "123 Main St, Seattle, WA",
            "battery_level": 87,
            "source_device_name": "Pixel 8",
        },
    }


def _status_snapshot_with_app_usage(app_usage_sessions=None):
    snapshot = _status_snapshot_with_location()
    snapshot["apps_and_usage"]["apps"] = [
        {"packageName": "com.google.android.youtube", "title": "YouTube"},
        {"packageName": "com.spotify.music", "title": "Spotify Kids"},
    ]
    snapshot["apps_and_usage"]["appUsageSessions"] = (
        app_usage_sessions
        if app_usage_sessions is not None
        else [
            {
                "date": {"year": 2026, "month": 7, "day": 16},
                "usage": "3600.0s",
                "appId": {"androidAppPackageName": "com.google.android.youtube"},
            },
            {
                "date": {"year": 2026, "month": 7, "day": 16},
                "usage": "300.0s",
                "appId": {"androidAppPackageName": "com.google.android.youtube"},
            },
            {
                "date": {"year": 2026, "month": 7, "day": 16},
                "usage": "900.0s",
                "appId": {"androidAppPackageName": "com.spotify.music"},
            },
            {
                "date": {"year": 2026, "month": 7, "day": 15},
                "usage": "1800.0s",
                "appId": {"androidAppPackageName": "com.discord"},
            },
        ]
    )
    return snapshot


class FakeAuthClient:
    def __init__(self, healthy=True, cookies=None):
        self._healthy = healthy
        self._cookies = cookies

    async def health_ok(self):
        return self._healthy

    async def get_cookies(self):
        return self._cookies


class FakeFamilyLinkApiClient:
    """Stands in for FamilyLinkApiClient(auth_client) in setup.py."""

    _discovered = [{"id": "child1", "name": "Kiddo"}]
    _raise = None

    def __init__(self, auth_client):
        pass

    async def authenticate(self):
        pass

    async def get_all_supervised_children(self):
        if FakeFamilyLinkApiClient._raise:
            raise FakeFamilyLinkApiClient._raise
        return FakeFamilyLinkApiClient._discovered


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def client(engine):
    app = FastAPI()
    app.include_router(status.router)
    app.include_router(setup.router)
    app.include_router(settings_web.router)
    app.include_router(history.router)

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, follow_redirects=False)


def _add_location_history(session: Session, child_id: str) -> None:
    timeline = [
        (
            datetime(2026, 7, 16, 16, 0, tzinfo=timezone.utc),
            {
                "latitude": 47.61,
                "longitude": -122.34,
                "accuracy": 35,
                "timestamp": "2026-07-16T16:00:00+00:00",
                "place_name": "Park",
            },
            {
                "latitude": 47.60,
                "longitude": -122.33,
                "accuracy": 50,
                "timestamp": "2026-07-16T15:55:00+00:00",
                "place_name": "School",
            },
        ),
        (
            datetime(2026, 7, 16, 16, 5, tzinfo=timezone.utc),
            {
                "latitude": 47.62,
                "longitude": -122.35,
                "accuracy": 25,
                "timestamp": "2026-07-16T16:05:00+00:00",
                "place_name": "Library",
            },
            {
                "latitude": 47.61,
                "longitude": -122.34,
                "accuracy": 35,
                "timestamp": "2026-07-16T16:00:00+00:00",
                "place_name": "Park",
            },
        ),
        (
            datetime(2026, 7, 16, 16, 10, tzinfo=timezone.utc),
            {
                "latitude": 47.63,
                "longitude": -122.36,
                "accuracy": 20,
                "timestamp": "2026-07-16T16:10:00+00:00",
                "place_name": "Home",
            },
            {
                "latitude": 47.62,
                "longitude": -122.35,
                "accuracy": 25,
                "timestamp": "2026-07-16T16:05:00+00:00",
                "place_name": "Library",
            },
        ),
    ]
    for detected_at, new_values, old_values in timeline:
        for field_name, new_value in new_values.items():
            session.add(ChangeEvent(
                child_id=child_id,
                field_path=f"location.{field_name}",
                old_value=old_values.get(field_name),
                new_value=new_value,
                detected_at=detected_at,
            ))
    session.add(LatestSnapshot(child_id=child_id, data={"location": {
        "latitude": 47.63,
        "longitude": -122.36,
        "accuracy": 20,
        "timestamp": "2026-07-16T16:10:00+00:00",
        "place_name": "Home",
    }}))


def test_setup_shows_auth_stage_when_unhealthy(monkeypatch, client):
    monkeypatch.setattr(setup, "build_auth_client", lambda: FakeAuthClient(healthy=False))
    resp = client.get("/setup")
    assert resp.status_code == 200
    assert "Waiting for the" in resp.text


def test_setup_discovers_children_when_authenticated(monkeypatch, client):
    monkeypatch.setattr(setup, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))
    monkeypatch.setattr(setup, "FamilyLinkApiClient", FakeFamilyLinkApiClient)
    resp = client.get("/setup")
    assert resp.status_code == 200
    assert "Kiddo" in resp.text


def test_setup_children_post_persists_and_notify_completes(monkeypatch, client, engine):
    monkeypatch.setattr(setup, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))
    monkeypatch.setattr(setup, "FamilyLinkApiClient", FakeFamilyLinkApiClient)

    resp = client.post("/setup/children", data={
        "child_ids": ["child1"],
        "child_names": ["Kiddo"],
        "enabled_child1": "on",
    })
    assert resp.status_code == 303
    assert resp.headers["location"] == "/setup"

    with Session(engine) as s:
        child = s.get(Child, "child1")
        assert child is not None
        assert child.name == "Kiddo"
        assert child.enabled is True

    # Next GET /setup should now show the notify stage (children already exist).
    resp2 = client.get("/setup")
    assert "ntfy" in resp2.text.lower()

    resp3 = client.post("/setup/notify", data={
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "my-secret-topic",
        "poll_interval_minutes": "15",
    })
    assert resp3.status_code == 303
    assert resp3.headers["location"] == "/settings"

    from app.db import settings_store
    with Session(engine) as s:
        assert settings_store.is_setup_completed(s) is True
        assert settings_store.get_ntfy_config(s) == ("https://ntfy.sh", "my-secret-topic")
        assert settings_store.get_poll_interval_minutes(s) == 15


def test_settings_page_renders_and_toggle_child(monkeypatch, client, engine):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))
    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo", enabled=True))
        s.commit()

    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Kiddo" in resp.text
    assert "enabled" in resp.text

    resp2 = client.post("/settings/children/child1/toggle")
    assert resp2.status_code == 303

    with Session(engine) as s:
        child = s.get(Child, "child1")
        assert child.enabled is False


def test_settings_reset_baseline_deletes_snapshot(client, engine):
    from app.db.models import LatestSnapshot

    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo", enabled=True))
        s.add(LatestSnapshot(child_id="child1", data={"apps_and_usage": {"apps": []}}))
        s.commit()

    resp = client.post("/settings/children/child1/reset-baseline")
    assert resp.status_code == 303

    with Session(engine) as s:
        assert s.get(LatestSnapshot, "child1") is None


def test_settings_toggle_auto_revoke_bonus_time(monkeypatch, client, engine):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))
    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo", enabled=True))
        s.commit()

    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Auto-revoke bonus time" in resp.text

    resp2 = client.post("/settings/children/child1/toggle-auto-revoke-bonus-time")
    assert resp2.status_code == 303

    with Session(engine) as s:
        child = s.get(Child, "child1")
        assert child.auto_revoke_bonus_time is True

    resp3 = client.post("/settings/children/child1/toggle-auto-revoke-bonus-time")
    assert resp3.status_code == 303

    with Session(engine) as s:
        child = s.get(Child, "child1")
        assert child.auto_revoke_bonus_time is False


def test_settings_page_lists_blocked_apps_and_toggle_always_blocked(monkeypatch, client, engine):
    from app.db.models import AppRule

    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))
    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo", enabled=True))
        s.add(AppRule(child_id="child1", package_name="com.tiktok.android", title="TikTok", always_blocked=False))
        s.commit()

    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "TikTok" in resp.text

    resp2 = client.post("/settings/children/child1/apps/com.tiktok.android/toggle-always-blocked")
    assert resp2.status_code == 303

    with Session(engine) as s:
        rule = s.get(AppRule, ("child1", "com.tiktok.android"))
        assert rule.always_blocked is True


def test_settings_page_notifications_toggle_persists(monkeypatch, client, engine):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    from app.db import settings_store

    resp = client.get("/settings")
    assert "notifications_enabled" in resp.text
    with Session(engine) as s:
        # No setting saved yet -- defaults to enabled.
        assert settings_store.get_notifications_enabled(s) is True

    # Submitting the form without the checkbox present (unchecked) disables it.
    resp2 = client.post("/settings", data={
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "my-topic",
        "poll_interval_minutes": "20",
    })
    assert resp2.status_code == 303
    with Session(engine) as s:
        assert settings_store.get_notifications_enabled(s) is False

    # Submitting with the checkbox present (checked) re-enables it.
    resp3 = client.post("/settings", data={
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "my-topic",
        "poll_interval_minutes": "20",
        "notifications_enabled": "on",
    })
    assert resp3.status_code == 303
    with Session(engine) as s:
        assert settings_store.get_notifications_enabled(s) is True


def test_settings_location_tracking_toggle_persists(monkeypatch, client, engine):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    from app.db import settings_store

    resp = client.get("/settings")
    assert resp.status_code == 200
    assert 'action="/settings/location-tracking/toggle"' in resp.text
    assert "Location tracking" in resp.text
    assert "Off by default for privacy" in resp.text
    toggle_form = resp.text.split('action="/settings/location-tracking/toggle"', 1)[1].split("</form>", 1)[0]
    assert "checked" not in toggle_form
    with Session(engine) as s:
        assert settings_store.get_location_tracking_enabled(s) is False

    resp2 = client.post("/settings/location-tracking/toggle", follow_redirects=False)
    assert resp2.status_code == 303
    assert resp2.headers["location"] == "/settings?saved=true"
    with Session(engine) as s:
        assert settings_store.get_location_tracking_enabled(s) is True

    resp3 = client.post("/settings/location-tracking/toggle", follow_redirects=False)
    assert resp3.status_code == 303
    assert resp3.headers["location"] == "/settings?saved=true"
    with Session(engine) as s:
        assert settings_store.get_location_tracking_enabled(s) is False


def test_settings_page_notification_categories_persist(monkeypatch, client, engine):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    from app.db import settings_store
    with Session(engine) as s:
        settings_store.set_location_tracking_enabled(s, True)

    # Not yet configured -- defaults to every category enabled, and every
    # checkbox should render checked.
    resp = client.get("/settings")
    assert 'name="category_app_blocking" checked' in resp.text
    assert 'name="category_screen_time" checked' in resp.text
    assert 'name="category_location" checked' in resp.text

    # Saving with only one category checked leaves just that one enabled.
    resp2 = client.post("/settings", data={
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "my-topic",
        "poll_interval_minutes": "20",
        "category_app_blocking": "on",
    })
    assert resp2.status_code == 303
    with Session(engine) as s:
        assert settings_store.get_enabled_notification_categories(s) == {"app_blocking"}

    resp3 = client.get("/settings")
    assert 'name="category_app_blocking" checked' in resp3.text
    assert 'name="category_screen_time" checked' not in resp3.text


def test_settings_page_hides_location_category_when_tracking_disabled(monkeypatch, client):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    resp = client.get("/settings")
    assert resp.status_code == 200
    assert 'name="category_location"' not in resp.text


def test_settings_page_shows_location_category_when_tracking_enabled(monkeypatch, client, engine):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    from app.db import settings_store

    with Session(engine) as s:
        settings_store.set_location_tracking_enabled(s, True)

    resp = client.get("/settings")
    assert resp.status_code == 200
    assert 'name="category_location" checked' in resp.text


def test_settings_page_timezone_persists(monkeypatch, client, engine):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    from app.db import settings_store

    resp = client.post("/settings", data={
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "my-topic",
        "poll_interval_minutes": "20",
        "timezone": "America/Los_Angeles",
    })
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings?saved=true"
    with Session(engine) as s:
        assert settings_store.get_timezone(s) == "America/Los_Angeles"

    resp2 = client.get("/settings", follow_redirects=True)
    assert 'value="America/Los_Angeles"' in resp2.text


def test_settings_page_rejects_invalid_timezone(monkeypatch, client, engine):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    from app.db import settings_store

    # Save a valid timezone first.
    client.post("/settings", data={
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "my-topic",
        "poll_interval_minutes": "20",
        "timezone": "America/Los_Angeles",
    })

    # An invalid one should be rejected, leaving the previously saved value
    # untouched, and flagged via the tz_error redirect param.
    resp = client.post("/settings", data={
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "my-topic",
        "poll_interval_minutes": "20",
        "timezone": "Not/ARealZone",
    })
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings?saved=true&tz_error=true"
    with Session(engine) as s:
        assert settings_store.get_timezone(s) == "America/Los_Angeles"


def test_poll_now_triggers_poll_and_redirects(monkeypatch, client):
    called = {"count": 0}

    async def fake_poll_once():
        called["count"] += 1

    monkeypatch.setattr(status, "poll_once", fake_poll_once)

    resp = client.post("/poll-now")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?polled=true"
    assert called["count"] == 1


def test_status_page_hides_location_and_battery_when_tracking_disabled(monkeypatch, client, engine):
    from app.db import settings_store
    from app.db.models import LatestSnapshot

    monkeypatch.setattr(status, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    with Session(engine) as s:
        settings_store.mark_setup_completed(s)
        settings_store.set_timezone(s, "UTC")
        s.add(Child(id="child1", name="Kiddo", enabled=True))
        s.add(LatestSnapshot(child_id="child1", data=_status_snapshot_with_location()))
        s.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Device activity" in resp.text
    assert "device-location-map" not in resp.text
    assert "battery-badge" not in resp.text


def test_build_app_usage_for_day_aggregates_sorts_and_falls_back_to_package_name():
    usage = status._build_app_usage_for_day(
        {
            "apps": [
                {"packageName": "com.google.android.youtube", "title": "YouTube"},
            ],
            "appUsageSessions": [
                {
                    "date": {"year": 2026, "month": 7, "day": 16},
                    "usage": "120.5s",
                    "appId": {"androidAppPackageName": "com.google.android.youtube"},
                },
                {
                    "date": {"year": 2026, "month": 7, "day": 16},
                    "usage": "59.5s",
                    "appId": {"androidAppPackageName": "com.google.android.youtube"},
                },
                {
                    "date": {"year": 2026, "month": 7, "day": 16},
                    "usage": "90.0s",
                    "appId": {"androidAppPackageName": "com.discord"},
                },
                {
                    "date": {"year": 2026, "month": 7, "day": 15},
                    "usage": "999.0s",
                    "appId": {"androidAppPackageName": "com.google.android.youtube"},
                },
            ],
        },
        _FROZEN_STATUS_NOW.date(),
    )

    assert [item["package_name"] for item in usage] == [
        "com.google.android.youtube",
        "com.discord",
    ]
    assert usage[0]["app_title"] == "YouTube"
    assert usage[0]["seconds"] == pytest.approx(180.0)
    assert usage[0]["duration_display"] == "3m"
    assert usage[1]["app_title"] == "com.discord"
    assert usage[1]["seconds"] == pytest.approx(90.0)


def test_status_page_shows_app_usage_chart_for_today(monkeypatch, client, engine):
    monkeypatch.setattr(status, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))
    monkeypatch.setattr(status, "datetime", _FrozenStatusDatetime)

    with Session(engine) as s:
        from app.db import settings_store

        settings_store.mark_setup_completed(s)
        settings_store.set_timezone(s, "UTC")
        s.add(Child(id="child1", name="Kiddo", enabled=True))
        s.add(LatestSnapshot(child_id="child1", data=_status_snapshot_with_app_usage()))
        s.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    assert "App usage today" in resp.text
    assert "YouTube" in resp.text
    assert "Spotify Kids" in resp.text
    assert "1h 5m" in resp.text
    assert "15m" in resp.text
    assert resp.text.index("YouTube") < resp.text.index("Spotify Kids")


def test_status_page_excludes_other_calendar_days_from_app_usage(monkeypatch, client, engine):
    monkeypatch.setattr(status, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))
    monkeypatch.setattr(status, "datetime", _FrozenStatusDatetime)

    with Session(engine) as s:
        from app.db import settings_store

        settings_store.mark_setup_completed(s)
        settings_store.set_timezone(s, "UTC")
        s.add(Child(id="child1", name="Kiddo", enabled=True))
        s.add(LatestSnapshot(child_id="child1", data=_status_snapshot_with_app_usage()))
        s.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    assert "com.discord" not in resp.text


def test_guest_status_hides_app_usage_without_screen_time_permission(monkeypatch, client, engine):
    monkeypatch.setattr(status, "datetime", _FrozenStatusDatetime)

    from app.db import settings_store
    from app.web import guest_permissions

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret", session_cookie="fla_session")
    app.include_router(status.router)
    app.include_router(auth.router)

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    guest_client = TestClient(app, follow_redirects=False)
    monkeypatch.setattr(status, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    with Session(engine) as session:
        session.add(Child(id="c1", name="Alice", enabled=True))
        session.add(LatestSnapshot(child_id="c1", data=_status_snapshot_with_app_usage()))
        settings_store.mark_setup_completed(session)
        settings_store.set_auth_enabled(session, True)
        settings_store.set_guest_view_enabled(session, True)
        settings_store.set_timezone(session, "UTC")
        guest_permissions.set_guest_permissions(session, {"page:status", "child:c1"})

    guest_client.get("/login/guest")
    resp = guest_client.get("/")
    assert resp.status_code == 200
    assert "App usage today" not in resp.text
    assert "YouTube" not in resp.text


def test_status_page_skips_empty_app_usage_chart(monkeypatch, client, engine):
    monkeypatch.setattr(status, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))
    monkeypatch.setattr(status, "datetime", _FrozenStatusDatetime)

    with Session(engine) as s:
        from app.db import settings_store

        settings_store.mark_setup_completed(s)
        settings_store.set_timezone(s, "UTC")
        s.add(Child(id="child1", name="Kiddo", enabled=True))
        s.add(LatestSnapshot(child_id="child1", data=_status_snapshot_with_app_usage(app_usage_sessions=[])))
        s.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Device activity" in resp.text
    assert "App usage today" not in resp.text


def test_status_page_shows_location_map_and_battery_when_present(monkeypatch, client, engine):
    from app.db import settings_store
    from app.db.models import LatestSnapshot

    monkeypatch.setattr(status, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    with Session(engine) as s:
        settings_store.mark_setup_completed(s)
        settings_store.set_timezone(s, "UTC")
        settings_store.set_location_tracking_enabled(s, True)
        s.add(Child(id="child1", name="Kiddo", enabled=True))
        s.add(LatestSnapshot(child_id="child1", data=_status_snapshot_with_location()))
        s.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    assert "device-location-map" in resp.text
    assert "battery-badge" in resp.text
    assert "/tiles/{z}/{x}/{y}.png" in resp.text
    assert "Last updated 2024-07-16 16:00" in resp.text
    assert "Home" in resp.text


def test_history_page_lists_events_and_failures(client, engine):
    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo"))
        s.add(ChangeEvent(child_id="child1", field_path="apps.tiktok.blocked", old_value=False, new_value=True))
        s.add(PollFailure(kind="auth_required", message="no cookies yet"))
        s.commit()

    resp = client.get("/history")
    assert resp.status_code == 200
    assert "apps.tiktok.blocked" in resp.text
    assert "Kiddo" in resp.text
    assert "auth_required" in resp.text


def test_history_page_filters_by_child(client, engine):
    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo"))
        s.add(Child(id="child2", name="Other Kid"))
        s.add(ChangeEvent(child_id="child1", field_path="apps.tiktok.blocked", old_value=False, new_value=True))
        s.add(ChangeEvent(child_id="child2", field_path="apps.roblox.blocked", old_value=False, new_value=True))
        s.commit()

    resp = client.get("/history", params={"child_id": "child1"})
    assert resp.status_code == 200
    assert "apps.tiktok.blocked" in resp.text
    assert "apps.roblox.blocked" not in resp.text

    resp_all = client.get("/history")
    assert "apps.tiktok.blocked" in resp_all.text
    assert "apps.roblox.blocked" in resp_all.text


def test_history_page_shows_location_icon_and_label(client, engine):
    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo"))
        s.add(ChangeEvent(child_id="child1", field_path="location.latitude", old_value=47.60, new_value=47.61))
        s.commit()

    resp = client.get("/history")
    assert resp.status_code == 200
    assert "icon-map-pin" in resp.text
    assert "Location" in resp.text
    assert "location.latitude" in resp.text


def test_history_page_links_to_location_history_only_when_child_has_location_events(client, engine):
    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo"))
        s.add(Child(id="child2", name="Other Kid"))
        s.add(ChangeEvent(child_id="child1", field_path="location.latitude", old_value=47.60, new_value=47.61))
        s.commit()

    resp_child1 = client.get("/history", params={"child_id": "child1"})
    assert resp_child1.status_code == 200
    assert "View location history" in resp_child1.text
    assert "view=location" in resp_child1.text

    resp_child2 = client.get("/history", params={"child_id": "child2"})
    assert resp_child2.status_code == 200
    assert "View location history" not in resp_child2.text


def test_history_location_view_renders_map_slider_and_fix_count(client, engine):
    from app.db import settings_store

    with Session(engine) as s:
        settings_store.set_timezone(s, "UTC")
        s.add(Child(id="child1", name="Kiddo"))
        _add_location_history(s, "child1")
        s.commit()

    resp = client.get("/history", params={"child_id": "child1", "view": "location"})
    assert resp.status_code == 200
    assert "Location history" in resp.text
    assert 'id="location-history-map"' in resp.text
    assert 'type="range"' in resp.text
    assert 'data-fix-count="3"' in resp.text
    assert "Park" in resp.text
    assert "Library" in resp.text
    assert "Home" in resp.text


def test_history_page_paginates_events(client, engine):
    from app.web.history import _PAGE_SIZE

    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo"))
        for i in range(_PAGE_SIZE + 5):
            s.add(ChangeEvent(child_id="child1", field_path=f"apps.app{i}.blocked", old_value=False, new_value=True))
        s.commit()

    resp = client.get("/history")
    assert resp.status_code == 200
    # Newest 50 (app{54} down to app{5}) on page 1; oldest 5 pushed to page 2.
    assert "apps.app4.blocked" not in resp.text
    assert "apps.app5.blocked" in resp.text
    assert "Page 1 of 2" in resp.text
    assert "55 total changes" in resp.text

    resp_p2 = client.get("/history", params={"page": 2})
    assert "apps.app4.blocked" in resp_p2.text
    assert "apps.app5.blocked" not in resp_p2.text
    assert "Page 2 of 2" in resp_p2.text

    # A page number beyond the last real page clamps back to the last page
    # instead of rendering an empty table.
    resp_over = client.get("/history", params={"page": 99})
    assert "Page 2 of 2" in resp_over.text


def test_toggle_theme_cycles_and_persists(client, engine):
    from app.db import settings_store

    resp = client.post("/toggle-theme", data={"next": "/settings"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings"
    with Session(engine) as s:
        assert settings_store.get_theme(s) == "light"

    client.post("/toggle-theme", data={"next": "/settings"})
    with Session(engine) as s:
        assert settings_store.get_theme(s) == "dark"

    # A subsequent GET of an unrelated page must reflect the persisted
    # theme (the actual bug being fixed -- theme used to only "stick" on
    # the Settings page because it was the only route that read it back).
    resp = client.get("/settings")
    assert 'var theme = "dark"' in resp.text


def test_toggle_theme_rejects_unsafe_next(client, engine):
    resp = client.post("/toggle-theme", data={"next": "//evil.example.com"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
