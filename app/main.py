"""FastAPI application entrypoint.

Wires up the web UI (first-run setup wizard, settings, history) and starts
the background poller on startup. Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
from starlette.middleware.sessions import SessionMiddleware

from .db import settings_store
from .db.migrate import run_migrations
from .db.session import get_engine
from .poller import start_scheduler
from .web import auth, history, settings as settings_web, setup, status, tiles

logging.basicConfig(level=logging.INFO)

# Migrations must run before anything below touches the DB -- including the
# session-secret lookup two lines down, which needs the `appsetting` table
# to already exist on a brand new install. Run eagerly at import time
# (rather than only in `lifespan`, which fires too late for
# SessionMiddleware -- middleware has to be attached to `app` before the
# ASGI server starts serving, at module-import time) -- calling
# `alembic upgrade head` twice (here and, historically, in `lifespan`) is
# harmless/idempotent, but only one call is needed, so `lifespan` below no
# longer calls it.
run_migrations()

with Session(get_engine()) as _session:
    _session_secret = settings_store.get_or_create_session_secret(_session)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler = start_scheduler()
    yield
    app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Family Link Alerts", lifespan=lifespan)

# Signs this app's own login session cookie (see app/web/auth.py,
# app/web/deps.py) -- unrelated to the Google account session, which is
# handled entirely by the familylink-auth container. A no-op in practice
# for installs that never turn auth on (no route ever writes to
# `request.session` in that case), but the middleware is always present so
# turning auth on later needs no restart-time wiring change.
app.add_middleware(SessionMiddleware, secret_key=_session_secret, session_cookie="fla_session")

# Self-hosted static assets (theme CSS, Lucide icon sprite, Space Grotesk
# font) -- no third-party CDN, so nothing about a user's browsing ever
# leaks to an external asset host.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

app.include_router(status.router)
app.include_router(setup.router)
app.include_router(settings_web.router)
app.include_router(history.router)
app.include_router(auth.router)
app.include_router(tiles.router)
