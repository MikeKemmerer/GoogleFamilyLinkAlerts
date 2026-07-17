"""Tests for this app's own optional login system: password hashing, the
login/logout/guest routes, role-gating (require_role/require_page_access),
and granular guest-visibility filtering on Status/History.

Uses its own TestClient fixture (rather than tests/test_web.py's) because
these tests need Starlette's SessionMiddleware installed, which the other
web tests deliberately don't need.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.middleware.sessions import SessionMiddleware

from app import security
from app.db import settings_store
from app.db.models import ChangeEvent, Child, LatestSnapshot, User
from app.web import auth, guest_permissions, history, settings as settings_web, status
from app.web.deps import get_db


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


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


class FakeAuthClient:
    async def health_ok(self):
        return False

    async def get_cookies(self):
        return None


@pytest.fixture
def client(engine, monkeypatch):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret", session_cookie="fla_session")
    app.include_router(status.router)
    app.include_router(settings_web.router)
    app.include_router(history.router)
    app.include_router(auth.router)

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(status, "build_auth_client", lambda: FakeAuthClient())
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient())

    with Session(engine) as session:
        settings_store.mark_setup_completed(session)
        session.commit()

    return TestClient(app, follow_redirects=False)


def make_admin(engine, username="admin", password="hunter2pass"):
    with Session(engine) as session:
        settings_store.set_auth_enabled(session, True)
        user = User(username=username, password_hash=security.hash_password(password), role="admin")
        session.add(user)
        session.commit()
        return user.id


def make_user(engine, username, password, role):
    with Session(engine) as session:
        user = User(username=username, password_hash=security.hash_password(password), role=role)
        session.add(user)
        session.commit()
        return user.id


def _add_guest_location_history(session: Session, child_id: str) -> None:
    detected_at = datetime(2026, 7, 16, 16, 10, tzinfo=timezone.utc)
    session.add(ChangeEvent(
        child_id=child_id,
        field_path="location.latitude",
        old_value=47.62,
        new_value=47.63,
        detected_at=detected_at,
    ))
    session.add(ChangeEvent(
        child_id=child_id,
        field_path="location.longitude",
        old_value=-122.35,
        new_value=-122.36,
        detected_at=detected_at,
    ))
    session.add(ChangeEvent(
        child_id=child_id,
        field_path="location.timestamp",
        old_value="2026-07-16T16:05:00+00:00",
        new_value="2026-07-16T16:10:00+00:00",
        detected_at=detected_at,
    ))
    session.add(ChangeEvent(
        child_id=child_id,
        field_path="location.place_name",
        old_value="Library",
        new_value="Home",
        detected_at=detected_at,
    ))
    session.add(LatestSnapshot(child_id=child_id, data={"location": {
        "latitude": 47.63,
        "longitude": -122.36,
        "timestamp": "2026-07-16T16:10:00+00:00",
        "place_name": "Home",
    }}))


# --- password hashing ----------------------------------------------------

def test_hash_and_verify_round_trip():
    hashed = security.hash_password("correct-password")
    assert security.verify_password("correct-password", hashed)
    assert not security.verify_password("wrong-password", hashed)


def test_hash_password_rejects_empty():
    with pytest.raises(ValueError):
        security.hash_password("")


def test_hash_password_rejects_too_long():
    with pytest.raises(ValueError):
        security.hash_password("x" * 73)


# --- login/logout/guest ----------------------------------------------------

def test_settings_reachable_without_login_when_auth_disabled(client):
    resp = client.get("/settings")
    assert resp.status_code == 200


def test_login_page_redirects_home_when_auth_disabled(client):
    resp = client.get("/login")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_login_success_and_failure(client, engine):
    make_admin(engine)
    bad = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert bad.status_code == 303
    assert "error=true" in bad.headers["location"]

    good = client.post("/login", data={"username": "admin", "password": "hunter2pass"})
    assert good.status_code == 303
    assert good.headers["location"] == "/"

    resp = client.get("/settings")
    assert resp.status_code == 200


def test_logout_clears_session(client, engine):
    make_admin(engine)
    client.post("/login", data={"username": "admin", "password": "hunter2pass"})
    client.post("/logout")
    resp = client.get("/settings")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_settings_redirects_to_login_when_auth_enabled_and_not_logged_in(client, engine):
    make_admin(engine)
    resp = client.get("/settings")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_guest_login_requires_guest_view_enabled(client, engine):
    make_admin(engine)
    resp = client.get("/login/guest")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    with Session(engine) as session:
        settings_store.set_guest_view_enabled(session, True)
    resp = client.get("/login/guest")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


# --- role gating -----------------------------------------------------------

def test_contributor_can_toggle_child_but_not_post_global_settings(client, engine):
    make_admin(engine)
    make_user(engine, "contrib", "contribpass123", "contributor")
    with Session(engine) as session:
        session.add(Child(id="c1", name="Kid", enabled=True))
        session.commit()

    client.post("/login", data={"username": "contrib", "password": "contribpass123"})

    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Access &amp; Users" not in resp.text
    assert "Connection" not in resp.text

    resp = client.post("/settings/children/c1/toggle")
    assert resp.status_code == 303

    resp = client.post("/settings", data={"ntfy_server": "https://x", "ntfy_topic": "t", "poll_interval_minutes": "20"})
    assert resp.status_code == 403


def test_contributor_cannot_toggle_location_tracking(client, engine):
    make_admin(engine)
    make_user(engine, "contrib", "contribpass123", "contributor")
    client.post("/login", data={"username": "contrib", "password": "contribpass123"})

    resp = client.post("/settings/location-tracking/toggle")
    assert resp.status_code == 403


def test_viewer_cannot_reach_settings(client, engine):
    make_admin(engine)
    make_user(engine, "viewer1", "viewerpass123", "viewer")
    client.post("/login", data={"username": "viewer1", "password": "viewerpass123"})
    resp = client.get("/settings")
    assert resp.status_code == 403


def test_viewer_can_reach_status_and_history(client, engine):
    make_admin(engine)
    make_user(engine, "viewer1", "viewerpass123", "viewer")
    client.post("/login", data={"username": "viewer1", "password": "viewerpass123"})
    assert client.get("/").status_code == 200
    assert client.get("/history").status_code == 200


# --- guest permission gating ------------------------------------------------

def test_guest_blocked_from_pages_with_no_permissions_granted(client, engine):
    make_admin(engine)
    with Session(engine) as session:
        settings_store.set_guest_view_enabled(session, True)
    client.get("/login/guest")

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    resp = client.get("/history", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_guest_sees_only_permitted_children_on_status_page(client, engine):
    make_admin(engine)
    with Session(engine) as session:
        session.add(Child(id="c1", name="Alice", enabled=True))
        session.add(Child(id="c2", name="Bob", enabled=True))
        settings_store.set_guest_view_enabled(session, True)
        guest_permissions.set_guest_permissions(session, {"page:status", "child:c1"})

    client.get("/login/guest")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Alice" in resp.text
    assert "Bob" not in resp.text


def test_guest_page_history_toggle_gates_history_independently(client, engine):
    make_admin(engine)
    with Session(engine) as session:
        settings_store.set_guest_view_enabled(session, True)
        guest_permissions.set_guest_permissions(session, {"page:status"})

    client.get("/login/guest")
    assert client.get("/", follow_redirects=False).status_code == 200
    resp = client.get("/history", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_guest_location_history_requires_location_permission(client, engine):
    make_admin(engine)
    with Session(engine) as session:
        session.add(Child(id="c1", name="Alice", enabled=True))
        _add_guest_location_history(session, "c1")
        settings_store.set_guest_view_enabled(session, True)
        settings_store.set_timezone(session, "UTC")
        guest_permissions.set_guest_permissions(session, {"page:history", "child:c1"})
        session.commit()

    client.get("/login/guest")
    resp = client.get("/history", params={"child_id": "c1", "location_child_id": "c1"})
    # Without "data:location", the whole location-history card is omitted
    # rather than erroring -- the rest of the page is still usable.
    assert resp.status_code == 200
    assert "Location history" not in resp.text
    assert 'id="location-history-map"' not in resp.text


def test_guest_location_history_allows_authorized_guest(client, engine):
    make_admin(engine)
    with Session(engine) as session:
        session.add(Child(id="c1", name="Alice", enabled=True))
        _add_guest_location_history(session, "c1")
        settings_store.set_guest_view_enabled(session, True)
        settings_store.set_timezone(session, "UTC")
        guest_permissions.set_guest_permissions(session, {"page:history", "child:c1", "data:location"})
        session.commit()

    client.get("/login/guest")
    resp = client.get("/history", params={"child_id": "c1", "location_child_id": "c1"})
    assert resp.status_code == 200
    assert "Location history" in resp.text
    assert 'id="location-history-map"' in resp.text


def test_guest_status_hides_map_when_location_permission_disabled(client, engine):
    from app.db.models import LatestSnapshot

    make_admin(engine)
    with Session(engine) as session:
        session.add(Child(id="c1", name="Alice", enabled=True))
        session.add(LatestSnapshot(child_id="c1", data=_status_snapshot_with_location()))
        settings_store.set_guest_view_enabled(session, True)
        settings_store.set_location_tracking_enabled(session, True)
        settings_store.set_timezone(session, "UTC")
        guest_permissions.set_guest_permissions(session, {"page:status", "child:c1", "data:battery"})

    client.get("/login/guest")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "device-location-map" not in resp.text
    assert "battery-badge" in resp.text


def test_guest_status_hides_battery_when_battery_permission_disabled(client, engine):
    from app.db.models import LatestSnapshot

    make_admin(engine)
    with Session(engine) as session:
        session.add(Child(id="c1", name="Alice", enabled=True))
        session.add(LatestSnapshot(child_id="c1", data=_status_snapshot_with_location()))
        settings_store.set_guest_view_enabled(session, True)
        settings_store.set_location_tracking_enabled(session, True)
        settings_store.set_timezone(session, "UTC")
        guest_permissions.set_guest_permissions(session, {"page:status", "child:c1", "data:location"})

    client.get("/login/guest")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "device-location-map" in resp.text
    assert "battery-badge" not in resp.text


def test_guest_status_shows_map_and_battery_when_both_permissions_enabled(client, engine):
    from app.db.models import LatestSnapshot

    make_admin(engine)
    with Session(engine) as session:
        session.add(Child(id="c1", name="Alice", enabled=True))
        session.add(LatestSnapshot(child_id="c1", data=_status_snapshot_with_location()))
        settings_store.set_guest_view_enabled(session, True)
        settings_store.set_location_tracking_enabled(session, True)
        settings_store.set_timezone(session, "UTC")
        guest_permissions.set_guest_permissions(session, {"page:status", "child:c1", "data:location", "data:battery"})

    client.get("/login/guest")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "device-location-map" in resp.text
    assert "battery-badge" in resp.text


def test_setup_admin_flow_enables_auth_and_logs_in(client, engine):
    with Session(engine) as session:
        assert settings_store.get_auth_enabled(session) is False

    resp = client.post("/settings/access/setup-admin", data={"username": "admin", "password": "hunter2pass"})
    assert resp.status_code == 303

    with Session(engine) as session:
        assert settings_store.get_auth_enabled(session) is True

    # The setup flow should have logged the new admin straight in.
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Access &amp; Users" in resp.text


def test_cannot_delete_last_admin(client, engine):
    admin_id = make_admin(engine)
    client.post("/login", data={"username": "admin", "password": "hunter2pass"})
    resp = client.post(f"/settings/access/users/{admin_id}/delete")
    assert resp.status_code == 303
    with Session(engine) as session:
        assert session.get(User, admin_id) is not None
