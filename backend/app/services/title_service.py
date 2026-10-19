"""Automatic conversation title generation (FR-N01–FR-N08)."""

from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import log_event
from app.core.metrics import TITLE_GEN
from app.infra.llm.base import ChatClient, ChatRequest
from app.repositories.messages import MessageRepository
from app.repositories.sessions import SessionRepository
from app.services.sync_service import SyncService

log = logging.getLogger("app.title")

TITLE_PROMPT = (
    "请为下面这段对话生成一个简短的标题，概括用户的主要意图。要求：使用与用户提问相同的语言；"
    "中文不超过 12 个字，英文不超过 6 个单词；不要使用引号、句号、表情或“标题：”前缀；只输出标题本身。"
)
_STRIP = re.compile(r"^[\s\"'“”‘’《》「」#*`]+|[\s\"'“”‘’《》「」。.!！?？#*`]+$")


def clean_title(raw: str, max_chars: int) -> str:
    line = next((ln for ln in raw.strip().splitlines() if ln.strip()), "")
    line = re.sub(r"^(标题|title)\s*[:：]\s*", "", line.strip(), flags=re.I)
    line = _STRIP.sub("", line)
    line = " ".join(line.split())
    return line[:max_chars]


def fallback_title(first_user: str, max_chars: int) -> str:
    t = " ".join(first_user.split())
    return t if len(t) <= max_chars else t[: max_chars - 1] + "…"


class TitleService:
    def __init__(
        self, sm: async_sessionmaker[AsyncSession], llm: ChatClient, sync: SyncService, settings: Settings
    ) -> None:
        self.sm = sm
        self.llm = llm
        self.sync = sync
        self.settings = settings

    async def maybe_generate(self, sid: str) -> str | None:
        """Generate a title if the session still has its default title. Never raises."""
        if not self.settings.title_generation_enabled:
            return None
        try:
            async with self.sm() as db:
                s = await SessionRepository(db).get(sid)
                if s is None or s.title_source != "default":
                    return None
                msgs = await MessageRepository(db).list_for_session(sid)
            user = next((m for m in msgs if m.role == "user"), None)
            assistant = next((m for m in msgs if m.role == "assistant"), None)
            if user is None:
                return None
            convo = f"用户：{user.content[:1500]}\n助手：{(assistant.content if assistant else '')[:1500]}"
            title = ""
            outcome = "ok"
            try:
                raw = await asyncio.wait_for(
                    self.llm.complete(
                        ChatRequest(
                            messages=[
                                {"role": "system", "content": TITLE_PROMPT},
                                {"role": "user", "content": convo},
                            ],
                            thinking=False,
                            max_tokens=40,
                            model=self.settings.deepseek_title_model,
                            temperature=0.3,
                        )
                    ),
                    timeout=self.settings.title_timeout_seconds,
                )
                title = clean_title(raw, self.settings.title_max_chars)
            except Exception as e:  # noqa: BLE001
                outcome = "fallback"
                log_event(log, "title.llm_failed", logging.WARNING, session_id=sid, error=type(e).__name__)
            if not title:
                outcome = "fallback"
                title = fallback_title(user.content, self.settings.title_max_chars)
            async with self.sm() as db:
                repo = SessionRepository(db)
                changed = await repo.rename(sid, title, "auto", only_if_source="default")
                await db.commit()
                row = await repo.get(sid) if changed else None
            if row is None:
                TITLE_GEN.labels("skipped").inc()
                return None
            TITLE_GEN.labels(outcome).inc()
            log_event(log, "title.generated", session_id=sid, outcome=outcome, chars=len(title))
            await self.sync.session_updated(row)
            return title
        except Exception as e:  # noqa: BLE001
            TITLE_GEN.labels("error").inc()
            log_event(log, "title.failed", logging.WARNING, session_id=sid, error=type(e).__name__)
            return None
