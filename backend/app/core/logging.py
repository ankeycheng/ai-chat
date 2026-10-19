"""Structured logging (NFR-06): JSON lines with trace_id/session_id/request_id."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{6})[A-Za-z0-9]+|(Bearer\s+)[^\s\"']+")


def redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: (m.group(1) + "***") if m.group(1) else (m.group(2) + "***"), text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None) or record.getMessage(),
            "msg": record.getMessage(),
            "trace_id": trace_id_var.get(),
            "session_id": getattr(record, "session_id", None) or session_id_var.get(),
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, ensure_ascii=False, default=str))


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        ctx = []
        for name, var in (("sid", session_id_var), ("rid", request_id_var)):
            v = (
                getattr(record, name.replace("sid", "session_id").replace("rid", "request_id"), None)
                or var.get()
            )
            if v:
                ctx.append(f"{name}={v[:8]}")
        extra = getattr(record, "fields", None)
        if extra:
            ctx.extend(f"{k}={v}" for k, v in extra.items() if k not in ("session_id", "request_id"))
        return redact(base + (" " + " ".join(ctx) if ctx else ""))


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if fmt == "json" else TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "uvicorn.access", "aiosqlite", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log a named structured event, e.g. log_event(log, "search.done", count=5)."""
    extra: dict[str, Any] = {"event": event, "fields": fields}
    for k in ("session_id", "request_id"):
        if k in fields:
            extra[k] = fields[k]
    logger.log(level, event, extra=extra)
