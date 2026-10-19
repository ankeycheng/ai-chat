"""Prometheus metrics (NFR-07, P1)."""

from prometheus_client import Counter, Gauge, Histogram

SSE_CONNECTIONS = Gauge("aichat_sse_connections", "Active SSE connections", ["channel"])
ACTIVE_RUNS = Gauge("aichat_active_runs", "Generation runs executing on this instance")
RUN_TERMINAL = Counter("aichat_run_terminal_total", "Runs reaching a terminal state", ["status"])
RUN_DURATION = Histogram(
    "aichat_run_duration_seconds", "Accept→terminal duration", buckets=(1, 2, 5, 10, 20, 40, 80, 160, 320)
)
FIRST_TOKEN = Histogram(
    "aichat_upstream_first_token_seconds",
    "Upstream request→first delta",
    buckets=(0.25, 0.5, 1, 2, 3, 5, 10, 20, 60),
)
SEARCH_DURATION = Histogram(
    "aichat_search_duration_seconds",
    "Search API latency",
    ["outcome"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16),
)
PUBLISH_DURATION = Histogram(
    "aichat_publish_seconds",
    "chunk→XADD application overhead",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.3, 1),
)
EVENTS_PUBLISHED = Counter("aichat_events_published_total", "Business events published", ["event"])
ERRORS = Counter("aichat_errors_total", "Errors by code", ["code"])
STREAM_LENGTH = Gauge("aichat_stream_length", "Length of the last written session stream")
TITLE_GEN = Counter("aichat_title_generation_total", "Title generation outcomes", ["outcome"])
