from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import __version__
from app.web import tiles


class _FakeResponse:
    def __init__(self, status_code=200, content=b"png-bytes", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


class _FakeHttpxClient:
    calls: list[tuple[str, dict | None]] = []
    response = _FakeResponse()
    exception: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, **kwargs):
        type(self).calls.append((url, headers))
        if type(self).exception is not None:
            raise type(self).exception
        return type(self).response


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tiles.settings, "app_data_dir", Path(tmp_path))
    app = FastAPI()
    app.include_router(tiles.router)
    return TestClient(app, follow_redirects=False)


def test_tile_proxy_returns_png_and_caches_to_disk(monkeypatch, client, tmp_path):
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.exception = None
    _FakeHttpxClient.response = _FakeResponse(status_code=200, content=b"\x89PNG test tile")
    monkeypatch.setattr(tiles.httpx, "AsyncClient", _FakeHttpxClient)

    resp = client.get("/tiles/3/4/5.png")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "public, max-age=86400"
    assert resp.content == b"\x89PNG test tile"
    assert _FakeHttpxClient.calls == [
        (
            "https://tile.openstreetmap.org/3/4/5.png",
            {
                "User-Agent": (
                    f"GoogleFamilyLinkAlerts/{__version__} "
                    "(self-hosted; https://github.com/MikeKemmerer/GoogleFamilyLinkAlerts)"
                ),
            },
        )
    ]
    assert (Path(tmp_path) / "tile_cache" / "3" / "4" / "5.png").read_bytes() == b"\x89PNG test tile"


def test_tile_proxy_rejects_out_of_range_coordinates(monkeypatch, client):
    _FakeHttpxClient.calls = []
    monkeypatch.setattr(tiles.httpx, "AsyncClient", _FakeHttpxClient)

    resp = client.get("/tiles/2/4/0.png")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid tile x: expected 0-3"
    assert _FakeHttpxClient.calls == []


def test_tile_proxy_returns_graceful_error_for_upstream_failure(monkeypatch, client):
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.exception = None
    _FakeHttpxClient.response = _FakeResponse(status_code=503, text="service unavailable")
    monkeypatch.setattr(tiles.httpx, "AsyncClient", _FakeHttpxClient)

    resp = client.get("/tiles/3/4/5.png")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Tile upstream returned HTTP 503"


def test_tile_proxy_serves_repeat_requests_from_disk_cache(monkeypatch, client):
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.exception = None
    _FakeHttpxClient.response = _FakeResponse(status_code=200, content=b"first copy")
    monkeypatch.setattr(tiles.httpx, "AsyncClient", _FakeHttpxClient)

    first = client.get("/tiles/3/4/5.png")
    _FakeHttpxClient.response = _FakeResponse(status_code=200, content=b"second copy")
    second = client.get("/tiles/3/4/5.png")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.content == b"first copy"
    assert second.content == b"first copy"
    assert len(_FakeHttpxClient.calls) == 1
