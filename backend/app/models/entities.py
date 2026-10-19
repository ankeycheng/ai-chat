"""ORM models — mirror the spec data model (migrations in alembic/versions are authoritative)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime

SESSION_STATUSES = ("idle", "generating")
RUN_STATUSES = ("accepted", "running", "completed", "failed", "interrupted")
ACTIVE_RUN_STATUSES = ("accepted", "running")
TERMINAL_RUN_STATUSES = ("completed", "failed", "interrupted")
TITLE_SOURCES = ("default", "auto", "user")


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    title_source: Mapped[str] = mapped_column(String(16), nullable=False, default="default")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_event_id: Mapped[str] = mapped_column(String(48), nullable=False, default="0-0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    active_request_id: Mapped[str | None] = mapped_column(String(36))
    next_sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint("status IN ('idle','generating')", name="status"),
        CheckConstraint("title_source IN ('default','auto','user')", name="title_source"),
        CheckConstraint(
            "(status = 'generating' AND active_request_id IS NOT NULL) OR "
            "(status = 'idle' AND active_request_id IS NULL)",
            name="active_request",
        ),
        Index("ix_sessions_updated_at", "updated_at"),
    )


class RunRow(Base):
    __tablename__ = "generation_runs"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    thinking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    web_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model: Mapped[str | None] = mapped_column(String(64))
    owner_token: Mapped[str | None] = mapped_column(String(64))
    instance_id: Mapped[str | None] = mapped_column(String(128))
    user_message_id: Mapped[str | None] = mapped_column(String(36))
    assistant_message_id: Mapped[str | None] = mapped_column(String(36))
    start_event_id: Mapped[str | None] = mapped_column(String(48))
    terminal_event_id: Mapped[str | None] = mapped_column(String(48))
    terminal_event_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    finish_reason: Mapped[str | None] = mapped_column(String(48))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    usage_json: Mapped[str | None] = mapped_column(Text)
    context_message_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        CheckConstraint("status IN ('accepted','running','completed','failed','interrupted')", name="status"),
        Index("idx_runs_status_heartbeat", "status", "heartbeat_at"),
        Index("idx_runs_unpublished", "terminal_event_published", "status"),
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reasoning_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    finish_reason: Mapped[str | None] = mapped_column(String(48))
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        CheckConstraint("role IN ('user','assistant')", name="role"),
        UniqueConstraint("session_id", "sequence_no", name="uq_messages_session_seq"),
        UniqueConstraint("session_id", "request_id", "role", name="uq_messages_session_request_role"),
        Index("idx_messages_session_sequence", "session_id", "sequence_no"),
    )
