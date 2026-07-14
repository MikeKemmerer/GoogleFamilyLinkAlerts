import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import poller
from app.db.models import Child, PollFailure
from app.familylink.exceptions import AuthenticationError, SessionExpiredError
from app.familylink.website_filter import WebsiteFilterNotImplementedError


class FakeApiClient:
    """Stands in for FamilyLinkApiClient in poller tests."""

    def __init__(self, apps_and_usage=None, time_limit=None, applied_time_limits=None,
                 raise_on_authenticate=None, raise_on_fetch=None):
        self._apps_and_usage = apps_and_usage or {"apps": []}
        self._time_limit = time_limit or {}
        self._applied_time_limits = applied_time_limits or {}
        self._raise_on_authenticate = raise_on_authenticate
        self._raise_on_fetch = raise_on_fetch

    async def authenticate(self):
        if self._raise_on_authenticate:
            raise self._raise_on_authenticate

    async def get_apps_and_usage(self, child_id):
        if self._raise_on_fetch:
            raise self._raise_on_fetch
        return self._apps_and_usage

    async def get_time_limit(self, child_id):
        return self._time_limit

    async def get_applied_time_limits(self, child_id, tz=None):
        return self._applied_time_limits


@pytest.fixture
def db_session(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(poller, "get_session", lambda: Session(engine))

    # website_filter is a stub -- patch it to raise the expected "not implemented"
    async def fake_website_filter(client, child_id):
        raise WebsiteFilterNotImplementedError()
    monkeypatch.setattr(poller, "get_website_filter", fake_website_filter)

    with Session(engine) as s:
        s.add(Child(id="child1", name="Kiddo"))
        s.commit()
    return engine


async def test_poll_once_first_snapshot_establishes_silent_baseline(monkeypatch, db_session):
    fake = FakeApiClient(applied_time_limits={"devices": {"dev1": {"remaining_minutes": 60}}})
    monkeypatch.setattr(poller, "build_api_client", lambda: fake)

    await poller.poll_once()

    with Session(db_session) as s:
        from app.db.models import ChangeEvent, LatestSnapshot
        snapshot = s.get(LatestSnapshot, "child1")
        assert snapshot is not None
        from sqlmodel import select
        events = s.exec(select(ChangeEvent)).all()
        # First-ever snapshot is a baseline, not a wall of "changed from None"
        # events -- see app/poller.py for rationale.
        assert events == []


async def test_poll_once_detects_change_on_second_poll(monkeypatch, db_session):
    fake1 = FakeApiClient(applied_time_limits={"devices": {"dev1": {"remaining_minutes": 60}}})
    monkeypatch.setattr(poller, "build_api_client", lambda: fake1)
    await poller.poll_once()

    fake2 = FakeApiClient(applied_time_limits={"devices": {"dev1": {"remaining_minutes": 30}}})
    monkeypatch.setattr(poller, "build_api_client", lambda: fake2)
    await poller.poll_once()

    from sqlmodel import select
    from app.db.models import ChangeEvent
    with Session(db_session) as s:
        events = s.exec(select(ChangeEvent)).all()
        changed = [e for e in events if e.field_path == "applied_time_limits.devices.dev1.remaining_minutes"]
        assert any(e.old_value == 60 and e.new_value == 30 for e in changed)


async def test_poll_once_records_auth_failure(monkeypatch, db_session):
    fake = FakeApiClient(raise_on_authenticate=AuthenticationError("no cookies"))
    monkeypatch.setattr(poller, "build_api_client", lambda: fake)

    await poller.poll_once()

    from sqlmodel import select
    with Session(db_session) as s:
        failures = s.exec(select(PollFailure)).all()
        assert len(failures) == 1
        assert failures[0].kind == "auth_required"


async def test_poll_once_records_session_expired(monkeypatch, db_session):
    fake = FakeApiClient(raise_on_fetch=SessionExpiredError("expired"))
    monkeypatch.setattr(poller, "build_api_client", lambda: fake)

    await poller.poll_once()

    from sqlmodel import select
    with Session(db_session) as s:
        failures = s.exec(select(PollFailure)).all()
        assert len(failures) == 1
        assert failures[0].kind == "session_expired"


async def test_poll_once_skips_ntfy_send_when_notifications_disabled(monkeypatch, db_session):
    from app.db import settings_store

    fake1 = FakeApiClient(applied_time_limits={"devices": {"dev1": {"remaining_minutes": 60}}})
    monkeypatch.setattr(poller, "build_api_client", lambda: fake1)
    await poller.poll_once()  # establishes baseline

    with Session(db_session) as s:
        settings_store.set_ntfy_config(s, "https://ntfy.sh", "topic")
        settings_store.set_notifications_enabled(s, False)

    sent = []

    class FakeNtfy:
        def __init__(self, *args, **kwargs):
            pass

        async def send(self, *args, **kwargs):
            sent.append((args, kwargs))
            return True

    monkeypatch.setattr(poller, "NtfyClient", FakeNtfy)

    fake2 = FakeApiClient(applied_time_limits={"devices": {"dev1": {"remaining_minutes": 30}}})
    monkeypatch.setattr(poller, "build_api_client", lambda: fake2)
    await poller.poll_once()

    assert sent == []

    from sqlmodel import select
    from app.db.models import ChangeEvent
    with Session(db_session) as s:
        events = s.exec(select(ChangeEvent)).all()
        changed = [e for e in events if e.field_path == "applied_time_limits.devices.dev1.remaining_minutes"]
        assert len(changed) == 1
        assert changed[0].notified is False
