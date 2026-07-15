"""Web UI tests: first-run setup wizard, settings page, history page.

Builds a minimal FastAPI app from the same routers as app.main but skips the
lifespan (migrations + scheduler) so tests don't touch the real global
settings singleton or spin up a background poller. Auth/API clients are
faked via monkeypatching the names each router module imported directly.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Child, ChangeEvent, PollFailure
from app.web import history, settings as settings_web, setup, status
from app.web.deps import get_db


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


def test_settings_page_notification_categories_persist(monkeypatch, client, engine):
    monkeypatch.setattr(settings_web, "build_auth_client", lambda: FakeAuthClient(healthy=True, cookies=[{"name": "SAPISID"}]))

    from app.db import settings_store

    # Not yet configured -- defaults to every category enabled, and every
    # checkbox should render checked.
    resp = client.get("/settings")
    assert 'name="category_app_blocking" checked' in resp.text
    assert 'name="category_screen_time" checked' in resp.text

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


def test_poll_now_triggers_poll_and_redirects(monkeypatch, client):
    called = {"count": 0}

    async def fake_poll_once():
        called["count"] += 1

    monkeypatch.setattr(status, "poll_once", fake_poll_once)

    resp = client.post("/poll-now")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?polled=true"
    assert called["count"] == 1


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
