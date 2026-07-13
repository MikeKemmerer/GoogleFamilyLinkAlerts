"""Shared FastAPI dependencies for the web UI."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from ..config import settings
from ..db.session import get_engine

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def get_db() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def build_auth_client():
    from ..familylink.auth_client import AuthClient
    return AuthClient(base_url=settings.familylink_auth_base_url, api_key=settings.familylink_auth_api_key)
