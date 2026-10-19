> 返回 [规格总入口](../spec.md)

## 11. 部署与配置

所有配置均来自环境变量（`pydantic-settings`，大小写不敏感；本地可用 `backend/.env`，已被 `.gitignore` 忽略）。`APP_PROFILE` 提供预设，显式设置的单项优先于预设。

**Profile 预设**

| `APP_PROFILE` | DB_BACKEND | BROKER_BACKEND | 日志格式 | 用途 |
|---|---|---|---|---|
| `dev`（默认） | `sqlite` | `memory` | text | 本地单进程开发，无需 Redis/PostgreSQL |
| `test` | `sqlite` | `memory` | text | 自动化测试（测试套件可显式切换到 postgres+redis） |
| `prod` | `postgres` | `redis` | json | 生产/多副本；禁止 memory 与 sqlite（[启动校验](#112-启动校验)） |

**配置项**（标注 ★ 的项为配置开关/本版关键项）

| 配置项 | 默认 | 说明 |
|---|---|---|
| `APP_PROFILE` ★ | `dev` | `dev`/`test`/`prod` |
| `DB_BACKEND` ★ | 随 profile | `postgres` 或 `sqlite`；若设置了 `DATABASE_URL` 且未设置本项，按 URL scheme 推断 |
| `DATABASE_URL` | — | 可选完整 URL；`postgresql://`/`postgres://` 自动规范化为 `postgresql+asyncpg://`，`sqlite://` 规范化为 `sqlite+aiosqlite://`；与 `DB_BACKEND` 冲突时启动失败 |
| `POSTGRES_HOST/PORT/USER/PASSWORD/DB` ★ | `localhost/5432/ai_chat/—/ai_chat` | 未设置 `DATABASE_URL` 时用于拼接 PostgreSQL URL（Compose 使用该方式） |
| `SQLITE_PATH` ★ | `./data/ai-chat.db` | SQLite 文件路径（启用 WAL、`busy_timeout`、`foreign_keys=ON`） |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` ★ | `10` / `20` | PostgreSQL 连接池 |
| `BROKER_BACKEND` ★ | 随 profile | `redis` 或 `memory`；兼容别名 `BROKER_IMPL`，两者冲突时启动失败 |
| `REDIS_URL` | `redis://localhost:6379/0` | `BROKER_BACKEND=redis` 时必需 |
| `REDIS_MAX_CONNECTIONS` ★ | `2000` | 连接池上限（阻塞读取使用独立连接池） |
| `EXPECTED_REPLICAS` ★ | `1` | 负载均衡后的 API 实例数（Compose 自动设置为副本数）；用于启动校验 |
| `WEB_CONCURRENCY` ★ | `1` | 每容器 uvicorn worker 数；用于启动校验 |
| `INSTANCE_ID` ★ | `hostname-pid` | 写入 run 与日志 |
| `RUN_MIGRATIONS_ON_STARTUP` ★ | `false` | 开发便利：启动时执行 `alembic upgrade head`（失败仅告警，`/api/ready` 反映 DB 状态）；生产由一次性 `migrate` 服务执行 |
| `LLM_PROVIDER` ★ | `deepseek` | `deepseek` 或 `fake`（离线开发/测试） |
| `DEEPSEEK_API_KEY` | — | `LLM_PROVIDER=deepseek` 时必填，仅服务端持有，绝不写入日志/仓库 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API base URL（Chat 与 Search 共用） |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 对话模型 |
| `DEEPSEEK_TITLE_MODEL` ★ | `deepseek-flash` | 标题模型 |
| `REASONING_EFFORT` ★ | `high` | 思考开启时的 `reasoning_effort`：`low/high/max`；思考关闭时固定发送 `none` |
| `SYSTEM_PROMPT` ★ | 中文助手提示 | 对话 system 提示词 |
| `MAX_OUTPUT_TOKENS` | `8192` | 每次生成上限（`deepseek-flash` 最大 384K） |
| `CONTEXT_MAX_INPUT_TOKENS` ★ | `100000` | 输入预算（[历史上下文组装规则](04-features.md#59-历史上下文组装规则p0)） |
| `CONTEXT_MAX_HISTORY_MESSAGES` ★ | `100` | 历史消息条数上限 |
| `UPSTREAM_CONNECT_TIMEOUT_SECONDS` | `10` | 上游连接超时 |
| `UPSTREAM_READ_TIMEOUT_SECONDS` | `120` | 上游读取空闲超时 |
| `SEARCH_PROVIDER` ★ | `deepseek` | `deepseek`/`disabled`/`fake` |
| `WEB_SEARCH_COUNT` | `5` | Search API `count`（1–50） |
| `WEB_CONTEXT_MAX_CHARS` | `12000` | 注入模型的搜索资料总字符上限 |
| `WEB_SOURCE_MAX_CHARS` ★ | `2500` | 单来源字符上限 |
| `SEARCH_TIMEOUT_SECONDS` ★ | `15` | Search 请求超时 |
| `TITLE_GENERATION_ENABLED` ★ | `true` | 自动标题开关 |
| `TITLE_MAX_CHARS` ★ | `24` | 标题硬上限 |
| `TITLE_TIMEOUT_SECONDS` ★ | `20` | 标题生成超时 |
| `DEFAULT_SESSION_TITLE` ★ | `新对话` | 默认标题 |
| `STREAM_TTL_SECONDS` | `86400` | 终态后会话 Stream TTL |
| `GLOBAL_STREAM_MAXLEN` ★ | `2000` | 全局事件流近似长度上限 |
| `LOCK_TTL_SECONDS` | `30` | 生成锁 TTL |
| `WORKER_HEARTBEAT_SECONDS` | `10` | 心跳/续租间隔 |
| `WORKER_STALE_SECONDS` | `45` | 心跳过期阈值 |
| `TERMINAL_REPAIR_INTERVAL_SECONDS` | `5` | 监控/修复扫描间隔 |
| `PING_INTERVAL_SECONDS` | `15` | SSE 心跳 |
| `SHUTDOWN_GRACE_SECONDS` ★ | `10` | 优雅关闭等待进行中任务的时间 |
| `MAX_CONTENT_CHARS` ★ | `32000` | 用户消息长度上限 |
| `CORS_ORIGINS` ★ | `http://localhost:5173,http://127.0.0.1:5173` | 逗号分隔精确白名单；经 nginx 同源访问时可留空 |
| `LOG_LEVEL` / `LOG_FORMAT` ★ | `INFO` / 随 profile | `json` 或 `text` |
| `METRICS_ENABLED` ★ | `true` | 暴露 `/api/metrics` |

Compose 专用（根目录 `.env`，见 `.env.example`）：`HTTP_PORT`（默认 9123）、`BACKEND_REPLICAS`（默认 2）、`POSTGRES_PASSWORD`（必填）。

### 11.1 负载均衡要求

- MUST：关闭响应缓冲（如 Nginx `proxy_buffering off`）、启用长连接、SSE 路径读取超时大于心跳间隔且留有足够余量、不启用会话粘性。
- SHOULD：设置 `X-Accel-Buffering: no`；禁用不适合 SSE 的压缩/缓存行为；为慢客户端设置合理写入超时和连接上限。
- 健康检查应区分 process liveness 与 dependency readiness。
- （`frontend/nginx.conf`）：`upstream` 使用 `zone` + `server backend:8000 resolve` + Docker DNS `resolver 127.0.0.11`，每个副本的 A 记录均参与 round-robin，副本重启/扩缩容后自动重新解析；`keepalive 64`；SSE 路径（`/api/sessions/*/stream`、`/api/events`）`proxy_buffering off`、`proxy_cache off`、`gzip off`、`proxy_read_timeout 3600s`，并透传 `Last-Event-ID`；普通 `/api/` 在连接错误/502/503 时 `proxy_next_upstream` 最多 2 次；`/api/metrics` 返回 403；SPA 路由回退 `index.html`；静态资源 `/assets/` 长缓存。响应头 `X-Upstream` 暴露实际上游地址，用于验证负载均衡（生产可删除）。
- SHOULD：生产在 nginx 前（或 nginx 本身）启用 HTTPS + HTTP/2。每个标签页常驻 2 条 SSE；HTTP/1.1 下浏览器对同源最多 6 条连接，同一浏览器打开 3 个以上活跃标签页会导致新请求排队。前端已在标签页隐藏 30 秒后释放 SSE 作为缓解，但不能替代 HTTP/2。

### 11.2 启动校验

- 启动时校验配置与 Redis/数据库连接池；必需依赖不可用时，readiness 返回失败，生产部署应避免将该实例继续接入新流量。
- 打印模型、Chat Completions、思考参数和 Search/Fetch 能力的配置探测结果，不打印 API Key。
- 生产多实例部署必须使用 Redis Broker，不得使用内存实现。
- `Settings.validate_startup()` 在应用创建时执行，下列任一条件成立即抛出 `ConfigError`、进程以非 0 退出（不会“带病启动”），错误信息说明修复方式：

| # | 条件 | 原因 |
|---|---|---|
| V1 | `BROKER_BACKEND=memory` 且（`EXPECTED_REPLICAS>1` 或 `WEB_CONCURRENCY>1`） | 内存 Broker 不跨进程，会导致广播/互斥失效 |
| V2 | `APP_PROFILE=prod` 且 `BROKER_BACKEND=memory` | 生产必须 Redis |
| V3 | `DB_BACKEND=sqlite` 且（`EXPECTED_REPLICAS>1` 或 `WEB_CONCURRENCY>1`） | SQLite 文件不能在多个容器间共享并保证锁语义 |
| V4 | `APP_PROFILE=prod` 且 `DB_BACKEND=sqlite` | 生产必须 PostgreSQL |
| V5 | `DB_BACKEND` 与 `DATABASE_URL` scheme 冲突；`BROKER_BACKEND` 与 `BROKER_IMPL` 冲突 | 避免误配 |
| V6 | `LOCK_TTL_SECONDS ≤ WORKER_HEARTBEAT_SECONDS` 或 `WORKER_STALE_SECONDS < LOCK_TTL_SECONDS`（另：`WORKER_STALE_SECONDS ≤ 2×心跳` 仅告警） | 续租前锁已过期 / 过早判定中断 |
| V7 | `LLM_PROVIDER=deepseek` 且未配置 `DEEPSEEK_API_KEY`（prod） | 无法生成 |

- 启动日志输出 `config.loaded`，内容为 `Settings.describe()`：profile、db/broker 后端、模型、标题模型、reasoning_effort、search_provider、`api_key_configured: true/false`、expected_replicas；**不含**任何密钥或密码（单测 `test_describe_never_contains_secret`）。
- 说明：本版的“多实例”校验依据配置声明（`EXPECTED_REPLICAS`/`WEB_CONCURRENCY`），无法发现“未声明却手动启动了多份”的情况；Compose 会自动传入副本数。

### 11.3 Docker 一键部署

| 文件 | 内容 |
|---|---|
| `docker-compose.yml` | `postgres:17-alpine`（`pg_isready` 健康检查，命名卷）、`redis:7-alpine`（AOF，`redis-cli ping` 健康检查）、一次性 `migrate`（`alembic upgrade head`，`service_completed_successfully` 门控）、`backend`（`deploy.replicas: ${BACKEND_REPLICAS:-2}`，`APP_PROFILE=prod`，`EXPECTED_REPLICAS` 同步副本数，`/api/ready` 健康检查，`stop_grace_period: 20s`）、`nginx`（前端静态 + 反向代理，端口 `${HTTP_PORT:-9123}`，依赖 backend healthy） |
| `backend/Dockerfile` | `python:3.13-slim` + `uv 0.13.1`；`uv sync --frozen --no-dev` 按 `uv.lock` 精确安装；非 root（uid 10001）；`HEALTHCHECK` 调用 `/api/ready`；`uvicorn --factory app.main:app_factory --proxy-headers --timeout-graceful-shutdown 15` |
| `frontend/Dockerfile` | `node:22-alpine` 执行 `npm ci && npm run build` → `nginx:1.27-alpine` 承载 `dist` 与 `nginx.conf` |
| `.env.example` | 所有可调变量及默认值；`DEEPSEEK_API_KEY` 与 `POSTGRES_PASSWORD` 留空/占位，不含任何真实密钥 |
| `deploy.sh` | 检查 docker / compose v2；无 `.env` 时由模板生成并提示填写；校验 Key 非空；`docker compose up -d --build`；轮询 `/api/ready` 最多 180 秒并输出结果；`./deploy.sh down` 停止；`ENV_FILE=/path/to/env ./deploy.sh` 可使用仓库外的环境文件 |
| `Makefile` | `make up/down/logs/ps`、`make dev-backend/dev-frontend`、`make test`、`make e2e`、`make ui-e2e` |

验收（MUST）：①一条命令启动后 5 个服务均为 healthy、`migrate` 正常退出；②经 nginx 连续请求 `/api/ready`，`X-Upstream` 在两个副本间交替；③全局流与会话流分别落在不同副本时，跨副本同步、续传无重复（`docs/e2e-report-docker-multireplica.json`）；④真实 DeepSeek e2e 经 nginx 全部通过（`docs/e2e-report-docker-pg-redis-2replicas.json`）；⑤`docker kill` 正在生成的副本后，存活副本在约 `WORKER_STALE_SECONDS` 后将任务标记为 `interrupted`，会话可继续发送。

### 11.4 数据库迁移

- 唯一迁移链 `backend/alembic/versions/`（文件 `0001_initial.py`，revision ID `0001_initial_v12`；revision ID 已记录在已部署数据库的 `alembic_version` 表中，不得修改），`alembic/env.py` 异步执行并从 `Settings` 取 URL；SQLite 使用 `render_as_batch`。
- 生产：Compose 的 `migrate` 服务在 backend 启动前执行；手动执行 `cd backend && uv run alembic upgrade head`。
- 开发：`RUN_MIGRATIONS_ON_STARTUP=true` 自动迁移。
- 每个迁移必须在 PostgreSQL 与 SQLite 上同时验证 `upgrade head` 与 `downgrade base`。

### 11.5 后端工程约定：uv

- 依赖、虚拟环境与命令全部由 uv 管理：`backend/pyproject.toml`（`[tool.uv] package = false`，应用不作为包安装）+ 提交到仓库的 `backend/uv.lock`。MUST
- 常用命令：`uv sync`（安装含 dev 依赖）、`uv run uvicorn --factory app.main:app_factory`、`uv run pytest`、`uv run ruff check .`、`uv run ruff format .`、`uv run alembic upgrade head`、`uv add <pkg>`（更新锁文件）。
- CI/镜像中使用 `uv sync --frozen`（锁文件与 pyproject 不一致即失败），不得使用 `pip install -r`。MUST
- Python 版本：`requires-python >= 3.13`，镜像与开发环境使用 3.13。
- 代码质量：`ruff check` 与 `ruff format --check` 零告警为合入条件（行宽 110）。
