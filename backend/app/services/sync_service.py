"""User-level (global) sync channel (spec v1.2 §5.10).

Every change to the conversation list (created / updated / deleted, incl. title,
status and updated_at) is appended to the global stream ``events:global`` so that all
windows — on any backend instance — converge. Events carry the full session payload
plus a monotonically increasing per-session ``version``; clients apply an event only
if its version is newer than what they hold.
"""

from __future__ import annotations

import json
import logging

from app.core.config import Settings
from app.core.logging import log_event
from app.infra.broker.base import GLOBAL_STREAM_KEY, Broker
from app.models.entities import SessionRow
from app.services.mappers import session_out

log = logging.getLogger("app.sync")


class SyncService:
    def __init__(self, broker: Broker, settings: Settings) -> None:
        self.broker = broker
        self.settings = settings

    async def _publish(self, event: str, payload: dict) -> str | None:
        try:
            return await self.broker.publish_capped(
                GLOBAL_STREAM_KEY,
                event,
                json.dumps(payload, ensure_ascii=False, default=str),
                self.settings.global_stream_maxlen,
            )
        except Exception as e:  # noqa: BLE001 - sync events are best-effort; clients resync on reconnect
            log_event(log, "sync.publish_failed", logging.WARNING, error=type(e).__name__)
            return None

    async def session_created(self, row: SessionRow) -> None:
        await self._publish("session.created", {"session": session_out(row).model_dump(mode="json")})

    async def session_updated(self, row: SessionRow) -> None:
        await self._publish("session.updated", {"session": session_out(row).model_dump(mode="json")})

    async def session_deleted(self, sid: str, version: int) -> None:
        await self._publish("session.deleted", {"id": sid, "version": version})
