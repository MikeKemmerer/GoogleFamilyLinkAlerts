import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import poller
from app.db.models import Child, PollFailure
from app.familylink.exceptions import AuthenticationError, SessionExpiredError
from app.familylink.website_filter import WebsiteFilterNotImplementedError


class FakeApiClient:
    """Stands in for FamilyLinkApiClient in poller tests."""

    def __init__(self, apps_and_usage=None, time_limit=None, applied_time_limits=None,
                 raise_on_authenticate=None, raise_on_fetch=None, raise_on_block=None):
        self._apps_and_usage = apps_and_usage or {"apps": []}
        self._time_limit = time_limit or {}
        self._applied_time_limits = applied_time_limits or {}
        self._raise_on_authenticate = raise_on_authenticate
        self._raise_on_fetch = raise_on_fetch
        self._raise_on_block = raise_on_block
        self.blocked: list[tuple[str, str]] = []

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

    async def block_app(self, child_id, package_name):
        if self._raise_on_block:
            raise self._raise_on_block
        self.blocked.append((child_id, package_name))


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


async def test_poll_once_discovers_blocked_app_into_app_rule(monkeypatch, db_session):
    apps_and_usage = {
        "apps": [
            {
                "title": "TikTok",
                "packageName": "com.tiktok.android",
                "installTimeMillis": "1756666561678",
                "supervisionSetting": {"hidden": True},
            },
            {
                "title": "Chrome",
                "packageName": "com.android.chrome",
                "installTimeMillis": "1756666561678",
                "supervisionSetting": {"hidden": False},
            },
        ]
    }
    fake = FakeApiClient(apps_and_usage=apps_and_usage)
    monkeypatch.setattr(poller, "build_api_client", lambda: fake)

    await poller.poll_once()

    from app.db.models import AppRule
    from sqlmodel import select
    with Session(db_session) as s:
        rules = s.exec(select(AppRule)).all()
        assert len(rules) == 1
        assert rules[0].package_name == "com.tiktok.android"
        assert rules[0].title == "TikTok"
        assert rules[0].always_blocked is False


async def test_poll_once_skips_preinstalled_system_apps_in_discovery(monkeypatch, db_session):
    # installTimeMillis == "0" marks OEM/carrier bloatware Family Link
    # hides by default -- these shouldn't clutter the Settings page list.
    apps_and_usage = {
        "apps": [
            {
                "title": "Samsung Knox internals",
                "packageName": "com.samsung.android.knox.containeragent",
                "installTimeMillis": "0",
                "supervisionSetting": {"hidden": True},
            },
            {
                "title": "TikTok",
                "packageName": "com.tiktok.android",
                "installTimeMillis": "1756666561678",
                "supervisionSetting": {"hidden": True},
            },
        ]
    }
    fake = FakeApiClient(apps_and_usage=apps_and_usage)
    monkeypatch.setattr(poller, "build_api_client", lambda: fake)

    await poller.poll_once()

    from app.db.models import AppRule
    from sqlmodel import select
    with Session(db_session) as s:
        rules = s.exec(select(AppRule)).all()
        assert [r.package_name for r in rules] == ["com.tiktok.android"]


async def test_poll_once_skips_oem_apps_with_nonzero_install_time(monkeypatch, db_session):
    # Some OEM apps (e.g. Samsung's "Reminder") get a real install
    # timestamp despite being pre-installed bloat -- filtered by package
    # prefix instead of install time in that case.
    apps_and_usage = {
        "apps": [
            {
                "title": "Reminder",
                "packageName": "com.samsung.android.app.reminder",
                "installTimeMillis": "1640995200000",
                "supervisionSetting": {"hidden": True},
            },
            {
                "title": "TikTok",
                "packageName": "com.tiktok.android",
                "installTimeMillis": "1756666561678",
                "supervisionSetting": {"hidden": True},
            },
        ]
    }
    fake = FakeApiClient(apps_and_usage=apps_and_usage)
    monkeypatch.setattr(poller, "build_api_client", lambda: fake)

    await poller.poll_once()

    from app.db.models import AppRule
    from sqlmodel import select
    with Session(db_session) as s:
        rules = s.exec(select(AppRule)).all()
        assert [r.package_name for r in rules] == ["com.tiktok.android"]


async def test_poll_once_reblocks_always_blocked_app_found_enabled(monkeypatch, db_session):
    from app.db.models import AppRule

    with Session(db_session) as s:
        s.add(AppRule(
            child_id="child1", package_name="com.tiktok.android",
            title="TikTok", always_blocked=True,
        ))
        s.commit()

    apps_and_usage = {
        "apps": [
            {
                "title": "TikTok",
                "appId": {"androidAppPackageName": "com.tiktok.android"},
                "supervisionSetting": {"hidden": False},
            },
        ]
    }
    fake = FakeApiClient(apps_and_usage=apps_and_usage)
    monkeypatch.setattr(poller, "build_api_client", lambda: fake)

    await poller.poll_once()

    assert fake.blocked == [("child1", "com.tiktok.android")]

    # The just-fetched snapshot is patched in place before being stored, so
    # the very same poll's stored snapshot already reflects "blocked again"
    # rather than waiting a full extra cycle to catch up.
    from app.db.models import LatestSnapshot
    with Session(db_session) as s:
        latest = s.get(LatestSnapshot, "child1")
        assert latest.data["apps_and_usage"]["apps"][0]["supervisionSetting"]["hidden"] is True


async def test_poll_once_skips_reblock_when_already_blocked(monkeypatch, db_session):
    from app.db.models import AppRule

    with Session(db_session) as s:
        s.add(AppRule(
            child_id="child1", package_name="com.tiktok.android",
            title="TikTok", always_blocked=True,
        ))
        s.commit()

    apps_and_usage = {
        "apps": [
            {
                "title": "TikTok",
                "appId": {"androidAppPackageName": "com.tiktok.android"},
                "supervisionSetting": {"hidden": True},
            },
        ]
    }
    fake = FakeApiClient(apps_and_usage=apps_and_usage)
    monkeypatch.setattr(poller, "build_api_client", lambda: fake)

    await poller.poll_once()

    assert fake.blocked == []


async def test_poll_once_logs_but_continues_when_reblock_fails(monkeypatch, db_session):
    from app.db.models import AppRule
    from app.familylink.exceptions import NetworkError

    with Session(db_session) as s:
        s.add(AppRule(
            child_id="child1", package_name="com.tiktok.android",
            title="TikTok", always_blocked=True,
        ))
        s.commit()

    apps_and_usage = {
        "apps": [
            {
                "title": "TikTok",
                "appId": {"androidAppPackageName": "com.tiktok.android"},
                "supervisionSetting": {"hidden": False},
            },
        ]
    }
    fake = FakeApiClient(apps_and_usage=apps_and_usage, raise_on_block=NetworkError("boom"))
    monkeypatch.setattr(poller, "build_api_client", lambda: fake)

    # Should not raise -- enforcement failures are logged, not fatal.
    await poller.poll_once()


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
