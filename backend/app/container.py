"""Composition root: wires infra adapters into services (dependency injection)."""

from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.engine import create_engine, create_sessionmaker
from app.infra.broker import create_broker
from app.infra.broker.base import Broker
from app.infra.llm import create_chat_client
from app.infra.llm.base import ChatClient
from app.infra.search import create_search_provider
from app.infra.search.base import SearchProvider
from app.services.chat_service import ChatService
from app.services.generation_worker import GenerationWorker
from app.services.monitor import RunMonitor
from app.services.search_service import SearchService
from app.services.session_service import SessionService
from app.services.stream_service import StreamService
from app.services.sync_service import SyncService
from app.services.tasks import TaskRegistry
from app.services.terminal_publisher import TerminalPublisher
from app.services.title_service import TitleService


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sm: async_sessionmaker[AsyncSession]
    broker: Broker
    llm: ChatClient
    search_provider: SearchProvider
    tasks: TaskRegistry = field(default_factory=TaskRegistry)

    def __post_init__(self) -> None:
        s = self.settings
        if not s.instance_id:
            s.instance_id = f"{socket.gethostname()}-{os.getpid()}"
        self.sync = SyncService(self.broker, s)
        self.sessions = SessionService(self.sm, self.broker, self.sync, s)
        self.terminal = TerminalPublisher(self.sm, self.broker, self.sync, s)
        self.titles = TitleService(self.sm, self.llm, self.sync, s)
        self.search = SearchService(self.search_provider, s)
        self.worker = GenerationWorker(
            self.sm, self.broker, self.llm, self.search, self.terminal, self.titles, s, self.tasks.spawn
        )
        self.chat = ChatService(self.sm, self.broker, self.sync, s, self.worker.start)
        self.streams = StreamService(self.sm, self.broker, s)
        self.monitor = RunMonitor(self.sm, self.terminal, s)
        self._monitor_task: asyncio.Task | None = None

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        llm: ChatClient | None = None,
        search_provider: SearchProvider | None = None,
        broker: Broker | None = None,
    ) -> Container:
        engine = create_engine(settings)
        return cls(
            settings=settings,
            engine=engine,
            sm=create_sessionmaker(engine),
            broker=broker or create_broker(settings),
            llm=llm or create_chat_client(settings),
            search_provider=search_provider or create_search_provider(settings),
        )

    def start_background(self) -> None:
        self._monitor_task = asyncio.create_task(self.monitor.run_forever(), name="monitor")

    async def shutdown(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
        await self.tasks.shutdown(self.settings.shutdown_grace_seconds)
        await self.llm.close()
        await self.search_provider.close()
        await self.broker.close()
        await self.engine.dispose()
