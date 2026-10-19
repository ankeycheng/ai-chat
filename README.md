# AI Chat：DeepSeek 流式聊天应用（实现 spec v0.1）

一个最小但完整的 AI 聊天应用：多会话、思考开关、联网搜索（DeepSeek 独立 Search API）、SSE 流式输出、刷新续传、多窗口实时同步（会话列表 + 消息）、会话搜索、自动标题，ChatGPT/Grok 风格界面并适配手机。后端可以多副本部署在无粘性负载均衡之后，也可以用 Docker 一键部署。

- 规格说明：[`docs/spec.md`](docs/spec.md)（总入口/索引；章节按功能拆在 [`docs/spec/`](docs/spec/)）
- 验收证据：`docs/e2e-report-*.json`、`docs/screenshots/*.png`

> ⚠️ **无鉴权**：本版本只适合内网或受控演示环境，不要直接暴露到公网（见 [已知限制](docs/spec/12-limitations-and-adrs.md)）。

---

## 1. 目录结构

```text
ai-chat/
├─ backend/                      Python 3.13 · FastAPI · uv
│  ├─ app/
│  │  ├─ api/routers/            HTTP 层：sessions / streams(SSE) / health
│  │  ├─ schemas/                Pydantic DTO
│  │  ├─ services/               业务层：接受流程、生成 worker、终态发布、监控、SSE、全局同步、标题、搜索、上下文
│  │  ├─ repositories/           数据访问（全部 SQL 都在这里）
│  │  ├─ infra/                  broker(redis|memory) · llm(deepseek|fake) · search(deepseek|fake|disabled)
│  │  ├─ models/ db/ core/       ORM 实体 · 引擎/迁移 · 配置/日志/错误/指标
│  │  ├─ container.py            组合根（按配置装配实现）
│  │  └─ main.py                 app_factory
│  ├─ alembic/                   迁移（PG 与 SQLite 共用一条链）
│  ├─ tests/                     pytest（sqlite-memory 与 postgres-redis 两种模式）
│  ├─ scripts/e2e_live.py        真实 DeepSeek 端到端脚本（只走 HTTP）
│  ├─ pyproject.toml · uv.lock · Dockerfile · .env.dev.example
├─ frontend/                     React 19.2 · Vite 8 · TypeScript
│  ├─ src/{api,lib,hooks,components,styles}
│  ├─ scripts/ui-e2e.mjs         无头 Chrome UI 端到端 + 截图
│  ├─ nginx.conf · Dockerfile
├─ docs/                         spec.md（入口）· spec/（分章）· e2e 报告 · screenshots/
└─ docker-compose.yml · deploy.sh · Makefile · .env.example
```

## 2. 本地开发（SQLite + 内存 Broker，不需要 Redis/PostgreSQL）

前置条件：[uv](https://docs.astral.sh/uv/) ≥ 0.8、Node.js ≥ 20。

```bash
# 后端：http://127.0.0.1:8000
cd backend
uv sync
export DEEPSEEK_API_KEY=sk-...          # 或写入 backend/.env（已被 git 忽略），模板见 .env.dev.example
APP_PROFILE=dev RUN_MIGRATIONS_ON_STARTUP=true \
  uv run uvicorn --factory app.main:app_factory --reload --port 8000

# 前端：http://127.0.0.1:5173（/api 自动代理到 8000）
cd frontend
npm ci
npm run dev
```

没有 Key 时，可以设置 `LLM_PROVIDER=fake SEARCH_PROVIDER=fake` 离线开发。也可以用 `make dev-backend` / `make dev-frontend` 启动。

本地切换到生产栈（PostgreSQL + Redis）：

```bash
APP_PROFILE=prod DATABASE_URL=postgresql://ai_chat:ai_chat@localhost:5432/ai_chat \
REDIS_URL=redis://localhost:6379/0 DEEPSEEK_API_KEY=sk-... \
uv run alembic upgrade head && uv run uvicorn --factory app.main:app_factory
```

## 3. 生产部署（Docker 一键）

前置条件：Docker 与 Compose v2。

```bash
cp .env.example .env      # 填写 DEEPSEEK_API_KEY 与 POSTGRES_PASSWORD
./deploy.sh               # 或 make up
# → http://<服务器IP>:9123
```

启动内容：`postgres:17` 和 `redis:7`（带健康检查和数据卷）、一次性的 `migrate`（`alembic upgrade head`）、**2 个 backend 副本**（`BACKEND_REPLICAS` 可调）、nginx（前端静态文件 + `/api` round-robin 反向代理，无会话粘性，SSE 不缓冲）。`deploy.sh` 会等待 `/api/ready` 就绪。其他命令：`./deploy.sh down`、`make logs`、`make ps`。

- 环境文件也可以放在仓库外：`ENV_FILE=/etc/ai-chat/prod.env ./deploy.sh`。
- `/api/metrics` 不能通过 nginx 访问（返回 403），请在内网直接抓取各副本的 8000 端口。
- 响应头 `X-Upstream` 显示实际处理请求的副本，用于验证负载均衡；生产环境可在 `frontend/nginx.conf` 中删除。
- **强烈建议**在前面加一层 HTTPS + HTTP/2（每个标签页常驻 2 条 SSE，HTTP/1.1 下每个源最多 6 个连接）。

## 4. 关键配置

完整列表见 [`docs/spec/10-deploy.md`](docs/spec/10-deploy.md)（入口仍为 [`docs/spec.md`](docs/spec.md)）。最常用的配置：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_PROFILE` | `dev` | `dev`/`test` → sqlite + memory；`prod` → postgres + redis + JSON 日志 |
| `DB_BACKEND` | 随 profile | `postgres` \| `sqlite` |
| `BROKER_BACKEND` | 随 profile | `redis` \| `memory`（`BROKER_IMPL` 为兼容别名） |
| `DATABASE_URL` / `POSTGRES_*` / `SQLITE_PATH` | — | 数据库连接 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis |
| `DEEPSEEK_API_KEY` | — | 必填（prod 缺失时拒绝启动） |
| `DEEPSEEK_MODEL` / `DEEPSEEK_TITLE_MODEL` | `deepseek-flash` | 对话模型 / 标题模型 |
| `REASONING_EFFORT` | `high` | 开启思考时使用 |
| `SEARCH_PROVIDER` | `deepseek` | `deepseek` \| `disabled` \| `fake` |
| `CONTEXT_MAX_INPUT_TOKENS` | `100000` | 历史上下文预算 |
| `EXPECTED_REPLICAS` / `WEB_CONCURRENCY` | `1` | 用于启动校验（Compose 会自动设置） |

**启动校验**：以下配置会直接启动失败：memory broker 或 SQLite 配合多副本/多 worker、prod 使用 memory/SQLite、`DB_BACKEND` 与 `DATABASE_URL` 冲突、锁 TTL 不大于心跳间隔、prod 缺少 Key。启动日志会打印不含任何密钥的配置摘要。

## 5. 架构要点

- 生成任务：`POST /messages` 先做幂等检查（`(session_id, request_id)` 唯一），再用 Redis 锁（`lock:gen:{sid}`，值含 owner token）竞争生成权，事务写入用户消息和 run 后返回 202，后台 worker 开始流式生成。
- 事件：所有增量都 `XADD` 到 `stream:{sid}`；SSE 端点用单一 `XREAD BLOCK` 循环完成回放和实时推送，并支持 `Last-Event-ID`。Stream ID 按数值比较。
- 终态：先落库再发布 `done`/`error`。终态发布用 Lua 保证幂等，修复扫描器负责补发；worker 用心跳续租，监控器把过期任务标记为 `interrupted`。`done` 事件额外携带完整 assistant `message`（与 DB 一致），前端可直接晋升临时气泡而无需再拉全量历史。
- 历史消息：`GET /api/sessions/{sid}/messages` 返回 `{items, oldest_sequence_no, newest_sequence_no, has_more_before}`；支持 `after_sequence_no`（增量）、`before_sequence_no`+`limit`（更早一页）、单独 `limit`（最近 N 条，打开会话默认 50）。
- 多窗口同步：会话变更在提交后写入 `events:global`，经 `/api/events` 推送。事件带 `version`，客户端按版本合并。
- 上下文：只纳入已完成的轮次，不回传 `reasoning_content`。搜索资料作为 system 消息放在当前问题之前，只用于本轮。超出预算时从最旧的轮次开始整轮丢弃。
- 联网搜索：应用侧调用 `POST https://api.deepseek.com/v1/tools/search`。失败时发送 `notice` 并降级为不联网继续回答，来源以 `sources` 事件和卡片形式展示。

## 6. 测试

```bash
cd backend
uv run ruff check . && uv run pytest -q   # 默认双模式共 126 个用例（sqlite-memory + postgres-redis）；另 frontend vitest 14；postgres-redis 需要本机 PG(ai_chat/ai_chat, 库 ai_chat_test) 与 Redis(db 15)
# 只跑 SQLite+内存模式： uv run pytest -q -k "not postgres-redis"

cd frontend
npm run typecheck && npm run lint && npm test && npm run build

# 真实 DeepSeek 端到端（服务需已启动并配置 Key）
cd backend && uv run python scripts/e2e_live.py --base-url http://127.0.0.1:8000 --report /tmp/e2e.json
# UI 端到端 + 截图（需要 Chrome/Chromium；CHROME_PATH 可指定路径）
cd frontend && node scripts/ui-e2e.mjs http://127.0.0.1:5173 ../docs/screenshots
```

## 7. 已知限制

- **无鉴权**，没有会话归属，只适合内网/演示。
- 生成任务**不会自动重跑**：实例崩溃后，已输出的片段可以回放，约 45 秒后任务被标记为“生成中断”，需要用户重新发送。
- **内存 Broker 只支持本地单进程**；多副本必须使用 Redis + PostgreSQL（启动校验会阻止误配）。
- **Redis Stream 只用于有限期回放**（终态后 24 小时 TTL）；已完成的内容以数据库为准。
- 不支持取消生成、编辑或重新生成消息，不支持文件上传。
- 联网搜索没有全局限流（DeepSeek 账户级限制为 5 QPS，超限时降级）；不使用 Fetch API。
- 会话搜索是子串匹配（不分词）；SQLite 下非 ASCII 字母区分大小写。
- 未执行 500 并发 SSE 压测，也未做真机移动端测试（只在无头 Chrome 中模拟 390×844）。
