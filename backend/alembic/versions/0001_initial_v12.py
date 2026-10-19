"""Initial schema for spec v1.2 (sessions / generation_runs / messages).

Same revision chain for PostgreSQL and SQLite; dialect-specific DDL is branched
explicitly below (spec v1.2 §7: no assumption that one DDL fits both).

Revision ID: 0001_initial_v12
Revises:
Create Date: 2026-10-19
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial_v12"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("title_source", sa.String(16), nullable=False, server_default="default"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("last_event_id", sa.String(48), nullable=False, server_default="0-0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="idle"),
        sa.Column("active_request_id", sa.String(36)),
        sa.Column("next_sequence_no", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.CheckConstraint("status IN ('idle','generating')", name="status"),
        sa.CheckConstraint("title_source IN ('default','auto','user')", name="title_source"),
        sa.CheckConstraint(
            "(status = 'generating' AND active_request_id IS NOT NULL) OR "
            "(status = 'idle' AND active_request_id IS NULL)",
            name="active_request",
        ),
    )
    op.create_index("ix_sessions_updated_at", "sessions", ["updated_at"])

    op.create_table(
        "generation_runs",
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("thinking", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("web_search", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("model", sa.String(64)),
        sa.Column("owner_token", sa.String(64)),
        sa.Column("instance_id", sa.String(128)),
        sa.Column("user_message_id", sa.String(36)),
        sa.Column("assistant_message_id", sa.String(36)),
        sa.Column("start_event_id", sa.String(48)),
        sa.Column("terminal_event_id", sa.String(48)),
        sa.Column("terminal_event_published", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("finish_reason", sa.String(48)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text),
        sa.Column("usage_json", sa.Text),
        sa.Column("context_message_count", sa.Integer),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("heartbeat_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("session_id", "request_id", name="pk_generation_runs"),
        sa.CheckConstraint(
            "status IN ('accepted','running','completed','failed','interrupted')", name="status"
        ),
    )
    op.create_index("idx_runs_status_heartbeat", "generation_runs", ["status", "heartbeat_at"])
    op.create_index("idx_runs_unpublished", "generation_runs", ["terminal_event_published", "status"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sequence_no", sa.BigInteger, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("reasoning_content", sa.Text, nullable=False, server_default=""),
        sa.Column("sources_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("finish_reason", sa.String(48)),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("role IN ('user','assistant')", name="role"),
        sa.UniqueConstraint("session_id", "sequence_no", name="uq_messages_session_seq"),
        sa.UniqueConstraint("session_id", "request_id", "role", name="uq_messages_session_request_role"),
    )
    op.create_index("idx_messages_session_sequence", "messages", ["session_id", "sequence_no"])

    if dialect == "postgresql":
        # case-insensitive search helpers (ILIKE); a pg_trgm GIN index is recommended but optional
        # because CREATE EXTENSION needs elevated privileges (spec v1.2 §5.11).
        op.execute("CREATE INDEX IF NOT EXISTS ix_sessions_title_lower ON sessions (lower(title))")
    elif dialect == "sqlite":
        op.execute("CREATE INDEX IF NOT EXISTS ix_sessions_title_lower ON sessions (lower(title))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sessions_title_lower")
    op.drop_index("idx_messages_session_sequence", table_name="messages")
    op.drop_table("messages")
    op.drop_index("idx_runs_unpublished", table_name="generation_runs")
    op.drop_index("idx_runs_status_heartbeat", table_name="generation_runs")
    op.drop_table("generation_runs")
    op.drop_index("ix_sessions_updated_at", table_name="sessions")
    op.drop_table("sessions")
