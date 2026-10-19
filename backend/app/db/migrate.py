"""Programmatic Alembic upgrade (used by dev auto-migrate and tests)."""

from __future__ import annotations

import asyncio
import os

from alembic.config import Config

from alembic import command
from app.core.config import Settings

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def alembic_config(settings: Settings) -> Config:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.attributes["sqlalchemy_url"] = settings.sqlalchemy_url
    return cfg


async def upgrade_head(settings: Settings) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")


def downgrade_base(settings: Settings) -> None:
    command.downgrade(alembic_config(settings), "base")
