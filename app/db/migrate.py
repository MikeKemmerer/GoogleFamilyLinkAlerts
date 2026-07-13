"""Run pending Alembic migrations programmatically.

Called on app startup (see app/main.py) so upgrading the `app` container to
a new version never requires a manual migration step -- schema changes ship
with the image and apply automatically against the persisted SQLite DB in
the data/ volume.
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

_LOGGER = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"


def run_migrations() -> None:
    """Apply any pending migrations up to 'head'. Safe to call every startup."""
    _LOGGER.info("Running database migrations (alembic upgrade head)...")
    cfg = Config(str(_ALEMBIC_INI))
    command.upgrade(cfg, "head")
    _LOGGER.info("Database migrations complete.")
