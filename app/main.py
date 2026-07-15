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

from .db.migrate import run_migrations
from .poller import start_scheduler
from .web import history, settings as settings_web, setup, status

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migrations run before anything else touches the DB, so an upgraded
    # image never starts serving against a stale schema.
    run_migrations()
    app.state.scheduler = start_scheduler()
    yield
    app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Family Link Alerts", lifespan=lifespan)

# Self-hosted static assets (theme CSS, Lucide icon sprite, Space Grotesk
# font) -- no third-party CDN, so nothing about a user's browsing ever
# leaks to an external asset host.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

app.include_router(status.router)
app.include_router(setup.router)
app.include_router(settings_web.router)
app.include_router(history.router)
