"""Application configuration (spec v1.2 §11).

Profiles (APP_PROFILE) provide preset defaults so one variable switches between
``dev`` (SQLite + memory broker, single process) and ``prod`` (PostgreSQL + Redis).
Any explicit variable overrides the profile preset.
"""

from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROFILE_PRESETS: dict[str, dict[str, str]] = {
    "dev": {"db_backend": "sqlite", "broker_backend": "memory", "log_format": "text"},
    "test": {"db_backend": "sqlite", "broker_backend": "memory", "log_format": "text"},
    "prod": {"db_backend": "postgres", "broker_backend": "redis", "log_format": "json"},
}


class ConfigError(ValueError):
    """Raised when the configuration violates a startup rule (§11.2)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",), env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_profile: Literal["dev", "test", "prod"] = "dev"
    instance_id: str = ""  # defaults to hostname-pid
    expected_replicas: int = Field(1, ge=1)  # number of API instances behind the LB
    web_concurrency: int = Field(1, ge=1)  # uvicorn workers per container

    # --- storage / broker switches (§11.3) ---
    db_backend: Literal["postgres", "sqlite"] | None = None
    database_url: str | None = None
    sqlite_path: str = "./data/ai-chat.db"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "ai_chat"
    postgres_password: SecretStr = SecretStr("ai_chat")
    postgres_db: str = "ai_chat"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    broker_backend: Literal["redis", "memory"] | None = None
    broker_impl: Literal["redis", "memory"] | None = None  # v1.1 alias
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 2000

    # --- DeepSeek ---
    llm_provider: Literal["deepseek", "fake"] = "deepseek"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_title_model: str = "deepseek-flash"
    reasoning_effort: Literal["low", "high", "max"] = "high"
    max_output_tokens: int = Field(8192, ge=1, le=393216)
    context_max_input_tokens: int = Field(100_000, ge=1000)
    context_max_history_messages: int = Field(100, ge=0)
    system_prompt: str = (
        "你是一个乐于助人的 AI 助手。回答使用 Markdown；代码放在带语言标注的代码块中。"
        "默认使用用户提问所用的语言回答。"
    )
    upstream_connect_timeout_seconds: float = 10
    upstream_read_timeout_seconds: float = 120

    # --- search (§5.4) ---
    search_provider: Literal["deepseek", "disabled", "fake"] = "deepseek"
    web_search_count: int = Field(5, ge=1, le=50)
    web_context_max_chars: int = Field(12000, ge=500)
    web_source_max_chars: int = Field(2500, ge=100)
    search_timeout_seconds: float = 15

    # --- title generation (§5.8) ---
    title_generation_enabled: bool = True
    title_max_chars: int = Field(24, ge=4, le=100)
    title_timeout_seconds: float = 20
    default_session_title: str = "新对话"

    # --- generation / stream lifecycle ---
    stream_ttl_seconds: int = 86400
    lock_ttl_seconds: int = 30
    worker_heartbeat_seconds: float = 10
    worker_stale_seconds: float = 45
    terminal_repair_interval_seconds: float = 5
    terminal_repair_grace_seconds: float = 3
    ping_interval_seconds: float = 15
    sse_block_ms: int = 5000
    last_event_id_flush_seconds: float = 2
    last_event_id_flush_events: int = 100
    idempotency_wait_seconds: float = 3
    global_stream_maxlen: int = 2000
    shutdown_grace_seconds: float = 10
    max_content_chars: int = 32000

    # --- misc ---
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] | None = None
    metrics_enabled: bool = True
    run_migrations_on_startup: bool = False  # dev convenience; prod uses one-shot migrate job

    @model_validator(mode="after")
    def _apply_profile(self) -> Settings:
        preset = PROFILE_PRESETS[self.app_profile]
        if self.broker_backend is None:
            self.broker_backend = self.broker_impl or preset["broker_backend"]  # type: ignore[assignment]
        if self.db_backend is None:
            if self.database_url:
                self.db_backend = "sqlite" if self.database_url.startswith("sqlite") else "postgres"
            else:
                self.db_backend = preset["db_backend"]  # type: ignore[assignment]
        if self.log_format is None:
            self.log_format = preset["log_format"]  # type: ignore[assignment]
        return self

    # ------------------------------------------------------------------ derived
    @cached_property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            url = self.database_url
            if url.startswith("postgresql://") or url.startswith("postgres://"):
                url = "postgresql+asyncpg://" + url.split("://", 1)[1]
            elif url.startswith("sqlite:///"):
                url = "sqlite+aiosqlite:///" + url.split(":///", 1)[1]
            return url
        if self.db_backend == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        pw = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{pw}@{self.postgres_host}:"
            f"{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_startup(self) -> list[str]:
        """Enforce §11.2 startup rules. Returns warnings; raises ConfigError on violations."""
        warnings: list[str] = []
        multi = self.expected_replicas > 1 or self.web_concurrency > 1
        if self.broker_backend == "memory" and multi:
            raise ConfigError(
                "BROKER_BACKEND=memory 仅支持单实例单进程；EXPECTED_REPLICAS/WEB_CONCURRENCY 必须为 1"
            )
        if self.broker_backend == "memory" and self.app_profile == "prod":
            raise ConfigError("APP_PROFILE=prod 必须使用 BROKER_BACKEND=redis")
        if self.is_sqlite and multi:
            raise ConfigError("DB_BACKEND=sqlite 不支持多实例/多进程部署，请改用 postgres")
        if self.is_sqlite and self.app_profile == "prod":
            raise ConfigError("APP_PROFILE=prod 必须使用 DB_BACKEND=postgres")
        if self.database_url and (self.db_backend == "sqlite") != self.is_sqlite:
            raise ConfigError("DB_BACKEND 与 DATABASE_URL 的协议不一致")
        if self.broker_impl and self.broker_backend != self.broker_impl:
            raise ConfigError("BROKER_BACKEND 与 BROKER_IMPL（v1.1 别名）取值冲突")
        if self.llm_provider == "deepseek" and not self.deepseek_api_key.get_secret_value():
            if self.app_profile == "prod":
                raise ConfigError("DEEPSEEK_API_KEY 未配置")
            warnings.append("DEEPSEEK_API_KEY 未配置：生成请求将失败（UPSTREAM_ERROR）")
        if self.worker_stale_seconds <= self.worker_heartbeat_seconds * 2:
            warnings.append("WORKER_STALE_SECONDS 应明显大于心跳间隔（建议 ≥ 4 倍）")
        if self.lock_ttl_seconds <= self.worker_heartbeat_seconds:
            raise ConfigError("LOCK_TTL_SECONDS 必须大于 WORKER_HEARTBEAT_SECONDS")
        if self.worker_stale_seconds < self.lock_ttl_seconds:
            raise ConfigError("WORKER_STALE_SECONDS 不得小于 LOCK_TTL_SECONDS")
        return warnings

    def describe(self) -> dict[str, object]:
        """Safe (secret-free) summary printed at startup."""
        return {
            "profile": self.app_profile,
            "db_backend": "sqlite" if self.is_sqlite else "postgres",
            "broker_backend": self.broker_backend,
            "llm_provider": self.llm_provider,
            "model": self.deepseek_model,
            "title_model": self.deepseek_title_model,
            "reasoning_effort": self.reasoning_effort,
            "search_provider": self.search_provider,
            "api_key_configured": bool(self.deepseek_api_key.get_secret_value()),
            "expected_replicas": self.expected_replicas,
        }


def get_settings() -> Settings:
    return Settings()
