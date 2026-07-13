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
