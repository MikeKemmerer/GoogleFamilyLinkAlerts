"""Tile proxy routes for the self-hosted device-location maps."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import __version__
from ..config import settings

router = APIRouter()

_LOGGER = logging.getLogger(__name__)
_CACHE_CONTROL = "public, max-age=86400"
_MAX_ZOOM = 19
_TILE_SERVER_BASE_URL = "https://tile.openstreetmap.org"
_TILE_TIMEOUT_SECONDS = 30.0
_TILE_USER_AGENT = (
    f"GoogleFamilyLinkAlerts/{__version__} "
    "(self-hosted; https://github.com/MikeKemmerer/GoogleFamilyLinkAlerts)"
)


def _validate_tile_coordinates(z: int, x: int, y: int) -> None:
    if not 0 <= z <= _MAX_ZOOM:
        raise HTTPException(status_code=400, detail=f"Invalid tile zoom: expected 0-{_MAX_ZOOM}")

    axis_limit = 1 << z
    if not 0 <= x < axis_limit:
        raise HTTPException(status_code=400, detail=f"Invalid tile x: expected 0-{axis_limit - 1}")
    if not 0 <= y < axis_limit:
        raise HTTPException(status_code=400, detail=f"Invalid tile y: expected 0-{axis_limit - 1}")


def _tile_cache_path(z: int, x: int, y: int) -> Path:
    return settings.app_data_dir / "tile_cache" / str(z) / str(x) / f"{y}.png"


def _tile_response(content: bytes) -> Response:
    return Response(content=content, media_type="image/png", headers={"Cache-Control": _CACHE_CONTROL})


@router.get("/tiles/{z}/{x}/{y}.png")
async def get_tile(z: int, x: int, y: int):
    _validate_tile_coordinates(z, x, y)

    cache_path = _tile_cache_path(z, x, y)
    if cache_path.is_file():
        return _tile_response(cache_path.read_bytes())

    url = f"{_TILE_SERVER_BASE_URL}/{z}/{x}/{y}.png"
    try:
        async with httpx.AsyncClient(timeout=_TILE_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers={"User-Agent": _TILE_USER_AGENT})
    except httpx.TimeoutException as err:
        _LOGGER.warning("Tile request to %s timed out: %s", url, err)
        raise HTTPException(status_code=504, detail="Tile upstream timed out") from err
    except httpx.HTTPError as err:
        _LOGGER.warning("Tile request to %s failed: %s", url, err)
        raise HTTPException(status_code=502, detail="Tile upstream request failed") from err

    if resp.status_code != 200:
        _LOGGER.warning("Tile upstream %s returned HTTP %s", url, resp.status_code)
        raise HTTPException(status_code=502, detail=f"Tile upstream returned HTTP {resp.status_code}")

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(resp.content)
    except OSError:
        _LOGGER.warning("Failed to write tile cache file %s", cache_path, exc_info=True)

    return _tile_response(resp.content)
