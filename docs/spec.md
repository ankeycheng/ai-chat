# AI 聊天应用开发规格说明书（Spec-Driven Development）

| 项 | 内容 |
|---|---|
| 文档版本 | v0.1（初始版本，规格驱动开发基线） |
| 状态 | 已实现并完成首轮验收（证据见 §13 与 `docs/`）；遗留项见 §14 |
| 依据 | 产品需求基线；DeepSeek 官方 API 文档 2026-10-17 版（见附录 A、附录 C） |
| 技术基线 | React 19.2 + Vite + TypeScript、Python 3.13 + FastAPI（uv 管理）、SQLAlchemy 2 async + Alembic、Redis Streams、PostgreSQL（本地可 SQLite）、DeepSeek Chat Completions + 独立 Search/Fetch API、Docker Compose + Nginx |
| 术语约定 | MUST/SHOULD/MAY 按 RFC 2119；P0/P1/P2 为交付优先级 |
| 读者 | 后端、前端、测试、运维 |

**版本历史**

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.1 | 2026-10-19 | 初始版本（规格驱动开发基线） |

> **范围说明**：本版不承诺任务自动故障转移或自动重跑；实例崩溃后应恢复已落在 Redis 的事件，并把任务明确标记为中断。核心能力包括：游标与多标签页恢复、生成互斥与幂等、终态事件、Redis 裁剪、任务崩溃检测、DeepSeek 搜索 API 契约；以及历史上下文组装、多窗口会话列表实时同步（全局 SSE）、会话搜索、会话重命名与自动标题、ChatGPT/Grok 风格 UI 与响应式布局、`DB_BACKEND`/`BROKER_BACKEND` 配置开关与启动校验、uv 管理的分层后端、Docker 一键部署。

---
## 1. 背景与目标

构建一个具备最小但完整能力的 AI 聊天应用：单/多会话、思考开关、联网搜索、流式输出、刷新续传、多端广播、多窗口会话列表实时同步、会话搜索、自动标题，提供 ChatGPT/Grok 风格且适配移动端的界面，并支持多实例负载均衡部署与 Docker 一键部署。

### 1.1 成功标准（验收总纲）

1. 在约定的测试环境及上游服务正常时，用户发送消息后首个正文或思考增量在 3s 内可见；应记录上游首 token 耗时和应用端耗时，不把上游延迟误算为应用端延迟。
2. 生成期间刷新页面，已落入 Redis Stream 的增量可回放，最终 UI 内容不丢失、不重复；正常完成后以数据库终态消息为准。
3. 同一会话在两个浏览器窗口中打开时，两边显示相同的消息顺序与最终内容。两个窗口可以分别建立 SSE 连接，但不能共享同一个客户端游标。
4. 后端至少 2 个实例、负载均衡不启用会话粘性时，以上能力仍成立。
5. 生成实例被强制终止时，已成功写入 Redis Stream 的增量可回放；任务监控在租约超时后将任务标记为 `interrupted`，前端明确提示“生成中断”。**不承诺自动恢复模型生成或从模型中间状态续跑。**
6. Redis、数据库和 API 均正常时，终态消息先持久化，客户端收到 `done` 后可通过历史接口读取对应完整 assistant 消息。
7. 任一窗口中创建、重命名、删除会话或会话标题被自动生成后，同一部署下所有已打开窗口的会话列表在 2 秒内（P95，局域网）一致；在一个窗口发送的消息（用户消息与流式回复）实时出现在打开同一会话的其他窗口中。跨实例时成立。
8. 会话搜索可按标题与消息正文找到会话，结果按最近活动排序，并展示命中片段。
9. 首轮对话正常完成后，会话标题在 30 秒内自动更新为模型生成的短标题（失败时为首条用户消息截断兜底），且所有窗口同步可见；用户手动重命名的标题不会被覆盖。
10. 在 390×844（手机）与 1440×900（桌面）视口下界面无横向滚动、核心操作（新建、切换、搜索、发送、展开思考）可用。
11. 在装有 Docker 的主机上，填写 `.env` 后一条命令（`./deploy.sh` 或 `make up`）启动 postgres、redis、2 个后端副本与 nginx，`/api/ready` 经 nginx 返回 200，上述 1–9 在该部署下成立。

---

## 2. 范围

**In Scope（P0）**：会话创建与列表、历史消息读取、消息发送、DeepSeek 流式生成、思考开关、联网搜索开关、SSE 回放与实时订阅、多实例广播、`request_id` 幂等、同会话生成互斥、基本错误处理、任务状态记录、结构化日志；历史上下文组装规则、多窗口会话列表实时同步（全局 SSE）、会话搜索、会话重命名、ChatGPT/Grok 风格 UI 与响应式布局、`DB_BACKEND`/`BROKER_BACKEND` 配置开关与启动校验、uv 管理的分层后端、Docker 一键部署。

**In Scope（P1）**：删除会话、生成中断检测与提示、心跳、`last_event_id` 节流更新、Prometheus 基础指标、增强型并发与性能测试；会话标题自动生成、搜索来源卡片展示、深浅色主题。

**Out of Scope（明确不做）**：用户账号与鉴权、消息编辑/重新生成、用户主动取消生成、文件上传、多模态、token 级数据库增量落库、任务故障转移自动重跑、限流计费、全文检索引擎（分词/相关度排序）、Fetch API 网页正文抓取（本版仅用 Search 返回的摘要/正文，见 §5.4）。当前版本仍为内网/受控演示用途；无鉴权时不得直接暴露到公网。

> 说明：任务状态表属于控制面元数据，不等同于把每个 token 增量写入数据库。进行中正文仍以 Redis Stream 为事件回放来源。

---

## 3. 术语

| 术语 | 定义 |
|---|---|
| Stream ID | Redis Stream 生成的 ID，形如 `1700000000000-10`；比较时必须先解析为两个非负整数并按数值比较，禁止直接做字符串大小比较 |
| 游标 `after` | SSE 订阅起点，exclusive：只投递 ID 大于 `after` 的 Stream 事件 |
| 业务事件 | 写入 Redis Stream 的 `start`、`delta`、`notice`、`done`、`error` 等事件；每个事件都有 Stream ID |
| 心跳 `ping` | 仅维持 SSE 连接的事件，不进入 Redis Stream，不推进客户端游标，不带 `id` |
| delta 事件 | 一个流式片段，含 `kind ∈ {reasoning, content}`、`text` 与 `request_id` |
| 临时 assistant 消息 | 前端根据当前生成任务的回放 delta 拼出的消息；正常结束后由终态消息确认或替换 |
| 终态消息 | 正常完成后写入 `messages` 表的完整 assistant 消息 |
| 生成任务 / Run | 一次用户发送请求，以 `(session_id, request_id)` 唯一标识，具有明确状态和生命周期 |
| 任务租约 / Heartbeat | 用于判断任务仍由某个 worker 持有和执行的短期有效记录；不得用 SSE ping 或 delta 间隔替代 |
| `event_key` | 业务事件逻辑标识，用于终态发布幂等；终态事件建议为 `terminal:{session_id}:{request_id}` |
| Broker | 事件发布/读取抽象；生产实现为 Redis Streams，内存实现仅用于单进程本地开发 |
| 终态发布协调 | 任务状态先持久化，再将 `done`/`error` 发布到 Redis；发布过程具备幂等与重试能力 |
| 全局事件流 | `events:global`，承载会话列表变更事件（`session.created/updated/deleted`），由 `/api/events` 以 SSE 推送给所有窗口 |
| 会话版本 `version` | `sessions.version`，会话行每次变更（标题、状态、`updated_at`、删除）单调 +1；客户端只接受版本更高的事件 |
| `title_source` | 标题来源：`default`（默认标题）、`auto`（自动生成或兜底）、`user`（手动重命名）；只有 `default` 会被自动标题覆盖 |
| 控制事件 | `ready`、`resync`、`ping`：由 SSE 端点直接产生、不写入 Stream、不带 `id` |
| 上下文预算 | 一次上游请求输入部分估算 token 数上限（`CONTEXT_MAX_INPUT_TOKENS`） |

---

## 4. 系统架构

### 4.1 组件

```text
Browser (React 19.2，桌面/移动)
  │ HTTPS(建议 HTTP/2) / SSE ×2：会话流 /api/sessions/{sid}/stream + 全局流 /api/events
  ▼
Nginx (静态前端 + /api 反向代理；proxy_buffering off；round-robin，无会话粘性)
  ├──▶ API Instance 1 ──┐
  └──▶ API Instance 2 ──┤
                        ├──▶ Redis (stream:{sid} 会话事件 + events:global 全局事件 + 生成锁 + 终态发布标记)
                        ├──▶ PostgreSQL / SQLite (sessions, messages, generation_runs)
                        ├──▶ DeepSeek Chat Completions API（对话生成、标题生成）
                        └──▶ DeepSeek Search API（仅 web_search=true 时）
```

### 4.2 核心原则（架构约束，实现不得违背）

- **A1**：进行中的正文增量以 `stream:{sid}` 中已成功写入的事件为回放来源；完整 assistant 消息以数据库为最终事实来源。任务状态元数据写入数据库。MUST
- **A2**：生成任务可以在任意 API 实例运行；所有业务事件必须通过 `XADD` 写入共享 Redis Stream，供所有实例上的订阅者读取。MUST
- **A3**：SSE 回放与实时读取必须由单一持续 `XREAD BLOCK` 循环完成，不允许先读一遍历史、再单独订阅实时事件。MUST
- **A4**：事件 ID 由 Redis 分配；应用层不得自行生成或递增 Stream ID。MUST
- **A5**：Broker 通过接口抽象，提供 Redis（生产）与内存（本地开发）两种实现。内存实现只保证同一进程语义，不承诺多实例广播。MUST
- **A6**：活动生成期间不得按固定 `MAXLEN` 裁剪当前任务的 delta；旧事件只能在前一任务已经进入终态、完整消息已落库后，于下一次生成开始前裁剪。MUST
- **A7**：终态状态必须先持久化，再发布终态事件；终态发布需要幂等与重试，避免数据库已完成但客户端永久收不到终态事件。MUST
- **A8**：任何实例不得把本实例内存中的订阅者集合或任务状态当作跨实例共享事实。MUST
- **A9**：会话列表变更（创建、重命名、自动标题、状态变化、删除）必须在数据库提交后发布到共享全局事件流 `events:global`；各实例的 `/api/events` 订阅者从该流读取，不得依赖实例内存广播。MUST
- **A10**：后端按 §4.3 分层；HTTP 层不得直接访问数据库或 Redis，基础设施实现（Redis/内存 Broker、PostgreSQL/SQLite、DeepSeek/Fake LLM、Search）只能经接口注入。MUST

```python
from typing import Protocol

class Broker(Protocol):
    async def publish(self, sid: str, event: str, data: str) -> str: ...
    async def read(
        self, sid: str, after: str, block_ms: int
    ) -> list[tuple[str, str, str]]: ...
```

`read` 返回的每一项为 `(stream_id, event_name, data_json)`。Redis 与内存实现必须遵守同一游标语义；内存实现无需模拟 Redis 的真实 ID 生成方式，但测试中必须保证 ID 有序且可比较。

本版完整 Broker 接口（两种实现均须通过同一组契约测试 `tests/test_brokers.py`）：

| 方法 | 语义 |
|---|---|
| `publish(key, event, data) -> id` | `XADD`；返回 Redis 分配的 ID |
| `publish_capped(key, event, data, maxlen) -> id` | `XADD MAXLEN ~ n`，仅用于全局事件流 |
| `read(key, after, block_ms)` | 单一阻塞读取（`XREAD BLOCK`），`after` exclusive |
| `first_id / last_id / exists / length` | 检测游标是否早于保留窗口、Stream 是否丢失 |
| `trim_before(key, min_id)` | `XTRIM MINID`：新一轮开始前裁剪到上一终态事件 |
| `expire(key, ttl)` / `persist(key)` | 终态后设置 TTL；新一轮开始时移除 TTL |
| `acquire_lock / renew_lock / release_lock / get_lock` | `SET NX EX`；续租/释放用 Lua 校验 owner |
| `publish_terminal(key, marker_key, event, data, ttl) -> (id, created)` | Lua 原子：标记存在则返回原 ID，否则 `XADD` + 写标记 |
| `delete_stream(key)` | 删除会话时清理 |
| `ping()` | 就绪检查 |

### 4.3 后端分层（MUST）

```text
backend/app/
├─ api/            HTTP 层：路由、请求/响应模型绑定、依赖注入、SSE 响应封装；不含业务规则
│  └─ routers/     sessions.py · streams.py · health.py
├─ schemas/        Pydantic 请求/响应 DTO（extra="forbid"）
├─ services/       业务层：chat_service(接受流程) · generation_worker · terminal_publisher · monitor
│                  stream_service · sync_service · session_service · title_service · search_service · context_builder
├─ repositories/   数据访问层：sessions · messages · runs（只做 SQL；条件更新/RETURNING 实现互斥后备）
├─ models/         SQLAlchemy 2 ORM 实体
├─ infra/          外部依赖适配：broker(redis|memory) · llm(deepseek|fake) · search(deepseek|fake|disabled)
├─ db/             引擎/会话工厂、方言参数、迁移入口
├─ core/           配置与启动校验、日志、错误码、指标、Stream ID 工具
└─ container.py    组合根：按配置装配实现并注入
```

规则：
- 依赖方向只能为 `api → services → repositories/infra → models/core`；反向依赖或跨层直连（例如路由中直接执行 SQL、服务中直接 `import redis`）视为违规。MUST
- `infra` 每类依赖都定义 Protocol，并提供生产实现与测试/本地实现：Broker（Redis / Memory）、LLM（DeepSeek / Fake）、Search（DeepSeek / Fake / Disabled）。MUST
- 业务服务不得读取环境变量，只能通过注入的 `Settings`。MUST
- 服务层可以持有 `AsyncSession` 作为事务边界（unit of work），但 SQL 语句只能出现在 `repositories/`；`api/` 不得出现 SQL（`/api/ready` 的 `SELECT 1` 探活除外）。MUST
- 验收：`rg "select\(|update\(|insert\(" app/services app/api` 无 SQL 构造；`rg "from app.api" app/services app/repositories app/infra` 无结果；`rg "import redis" app --glob '!app/infra/**'` 无结果。

---

## 5. 功能需求

### 5.1 会话管理

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-S01 | 创建会话，返回完整会话对象（§8.1 `Session`）；请求体可选 `title`，缺省为 `DEFAULT_SESSION_TITLE`（“新对话”），`title_source=default` | P0 | POST 返回 201，数据库有记录，初始 `status=idle`、`version=1`；发布 `session.created` |
| FR-S02 | 列出会话，**按 `updated_at` 倒序**（同值按 `id` 倒序），含 `last_event_id`、`status`、`active_request_id`、`title_source`、`updated_at`、`version`；支持 `limit`（1..200，默认 50）与不透明游标 `cursor` 分页，返回 `{items,next_cursor}` | P0 | 字段齐全；翻页无重复/遗漏；`last_event_id` 只代表服务端记录的会话 Stream 游标，不代表某个客户端已处理的游标 |
| FR-S03 | 获取会话历史消息，包含 `content`、`reasoning_content`、`request_id`、`sequence_no` | P0 | 按 `sequence_no` 升序返回；同时间戳下顺序稳定 |
| FR-S04 | 删除会话，级联删除消息与任务记录，并清理 Redis 会话 Stream | P1 | 仅允许删除 `idle` 会话；生成中返回 409 `SESSION_BUSY`；删除后无任务继续向该会话写入；返回 204 并发布 `session.deleted` |
| FR-S05 | 重命名会话 `PATCH /api/sessions/{sid}` `{title}`（去空白后 1..200 字符） | P0 | 返回更新后会话；`title_source=user`、`version+1`、`updated_at` 更新为当前时间（重命名视为一次活动，会话上浮到列表顶部）；发布 `session.updated` |
| FR-S06 | 会话搜索 `GET /api/sessions?q=`：在会话标题与消息正文（user 与 assistant 的 `content`）中做大小写不敏感的子串匹配，详见 §5.11 | P0 | 见 T30/T31 |
| FR-S07 | 首轮完成后自动生成标题，详见 §5.10 | P1 | 见 T28/T29 |
| FR-S08 | 任何会话变更（创建、重命名、自动标题、开始/结束生成、删除）都推进 `version` 并经全局事件流广播，详见 §5.8 | P0 | 见 T26/T27 |

### 5.2 消息发送与生成

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-M01 | POST 消息后立即返回 202；生成不阻塞 HTTP 请求 | P0 | 数据校验、幂等预留和用户消息/任务记录提交完成后返回；接口 P99 < 200ms（不含上游生成） |
| FR-M02 | 只有成功取得同会话生成权后，才能写入用户消息并接受任务 | P0 | 已有任务时返回 409，数据库不新增该次冲突请求的用户消息 |
| FR-M03 | 历史组装按 DeepSeek 当前 Chat Completions 协议构造消息，规则见 §5.9；本版不启用工具调用，因此不回传 `reasoning_content`；若未来启用工具调用，必须完整保留协议要求的 assistant/tool 轮次字段（含 `reasoning_content`） | P0 | T24/T25 通过；连续多轮思考测试不报参数错误 |
| FR-M04 | 每个已接受任务最终必须收敛到 `completed`、`failed` 或 `interrupted` 之一；终态事件通过幂等发布与修复任务确保最终可见 | P0 | 正常/可捕获异常/进程崩溃测试均有唯一逻辑终态；不要求被 kill 的原进程自己发布事件 |
| FR-M05 | 正常结束时在数据库事务中写入完整 assistant 消息，并将任务状态标记为 `completed` | P0 | `done` 事件发布前，历史接口已能读取完整消息；最终消息与所有 delta 拼接内容一致 |
| FR-M06 | 同一会话同时只允许一个被接受的生成任务；冲突返回 409 | P0 | 并发请求只允许一个成功取得生成权；冲突请求不插入 user message |
| FR-M07 | 相同 `(session_id, request_id)` 重复提交幂等，返回首次接受结果，不重复生成 | P0 | 并发双提交只创建一条 user message、最多一条 assistant message、一个生成任务；同 key 不同请求内容返回 409 `IDEMPOTENCY_KEY_REUSED` |
| FR-M08 | 接受任务后记录生成状态和活动请求 ID；实例异常退出后由监控器将过期任务标记为 `interrupted` | P1 | 超过任务租约阈值后状态收敛，UI 明确显示中断；不自动重跑 |

#### 生成任务接受顺序（MUST）

1. 校验请求体、会话是否存在、内容长度与 `request_id` 格式。
2. 查询 `(session_id, request_id)` 是否已存在：若请求内容 hash 相同，返回首次接受结果；若 hash 不同，返回 409 `IDEMPOTENCY_KEY_REUSED`。
3. 检查会话状态，并使用 Redis `SET lock:gen:{sid} <owner_token> NX EX <ttl>` 竞争生成权。获取锁失败时返回 409，不插入用户消息。
4. 在一个数据库事务中插入 user message、`generation_runs` 记录，并将会话设为 `generating`、填入 `active_request_id`。事务失败时必须安全释放自己持有的锁。
5. 提交事务后返回 202，并在当前实例启动后台生成协程。会话 `status/active_request_id/updated_at/version` 的变化随后通过 `session.updated` 广播。
6. worker 将旧 Stream 历史裁剪到保留最近一个已终态事件的程度，发布 `start` 事件，将其 ID 写入任务的 `start_event_id` 后，才可以调用上游模型。若该持久化步骤失败，不得开始生成。`start` 事件 data 携带本轮 `user_message`（完整消息对象），使其他窗口无需额外请求即可实时显示用户消息；若 `web_search=true`，先执行搜索（§5.4），再按 §5.9 组装上下文。
7. 每收到一个上游增量，先将其写入 Redis Stream，再由 SSE 订阅者读取。`reasoning_content` 和 `content` 分别映射为 `kind=reasoning` 与 `kind=content`。
8. 正常结束：在数据库事务中保存完整 assistant 消息、将 run 标记为 `completed` 并记录 `finish_reason`。随后通过终态发布器发布 `done`；成功后设置任务 `terminal_event_published=true`、清空会话 `active_request_id`、将会话置为 `idle`，最后释放自己的锁。
9. 可捕获异常：将 run 标记为 `failed` 并持久化安全的错误码/消息，之后幂等发布 `error`；成功发布后清理会话生成状态并释放锁。不写 assistant 完整消息，但保留用户消息和失败任务记录。
10. 进程崩溃：原进程无法执行收尾；监控器发现任务心跳过期后，通过数据库条件更新竞争将任务置为 `interrupted`，再由终态发布器发布 `error`（`code=GENERATION_INTERRUPTED`）。不得依赖前端仅凭无 delta 超时来判定任务死亡。

**锁要求（MUST）**：

- 锁值必须同时包含当前 `request_id` 与随机且唯一的 `owner_token`，用于区分同一请求的并发重复提交和不同请求的会话冲突。
- 锁值格式为 `{request_id}:{owner_token}`。
- 默认锁 TTL 30 秒，worker 每 10 秒续租；心跳/续租只允许匹配当前 owner token 的任务执行。
- 续租与释放必须用 Lua 或等价原子操作校验 owner token。禁止无条件 `DEL lock:gen:{sid}`。
- 监控器发现任务无心跳达到 45 秒后才可将其标记为中断；状态更新必须使用条件更新/事务，避免与仍活跃 worker 竞争。
- 任务终态发布未完成前，会话仍视为忙碌；即使锁意外过期，也必须由数据库中的 `active_request_id/status` 阻止新的任务插入。

**幂等要求（MUST）**：

- 幂等作用域为 `(session_id, request_id)`。
- 数据库对 `(session_id, request_id)` 的任务记录建立唯一约束；messages 对 `(session_id, request_id, role)` 建立唯一约束。
- Redis 幂等 Key 不作为唯一事实来源；即使 Key 过期，数据库唯一约束仍须阻止重复任务。
- 相同 key、相同 payload 返回原任务信息；相同 key、不同 payload 返回 409。若同一 request_id 的并发重复请求发生在首个数据库事务提交前，后到请求在发现 Redis 锁的 request_id 相同后，应短暂查询首次请求的最终接受结果；不得自行启动第二个 worker。若首次请求失败且没有创建任务，允许后到请求重新竞争生成权。
- 客户端重试必须复用原 `request_id`；用户明确发起新一轮或重试新任务时生成新的 UUID。

### 5.3 思考开关

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-T01 | `thinking=true` 时显式启用思考模式，并按配置传入 `reasoning_effort` | P0 | 上游请求参数正确 |
| FR-T02 | `thinking=false` 时显式关闭思考模式，不得依赖服务端默认值 | P0 | 上游请求使用 `thinking.type=disabled`；不得泄漏非预期 reasoning 增量 |
| FR-T03 | 将 `delta.reasoning_content` 映射为 `kind=reasoning`，将 `delta.content` 映射为 `kind=content` | P0 | 两类事件分流正确，允许任一字段在某个 chunk 中为空或缺失 |
| FR-T04 | 前端将 reasoning 折叠展示，默认收起 | P1 | 不干扰正文阅读；关闭思考模式时不显示空思考区块 |
| FR-T05 | 多轮历史必须遵循实际选定的 API 协议；若未启用工具调用，无需强制回传不被该模式使用的 reasoning 字段 | P0 | 对照官方模型协议的多轮测试通过 |

请求参数基线（Chat Completions）：

```python
# thinking=True
extra_body = {"thinking": {"type": "enabled"}}
reasoning_effort = "high"  # 配置项，可为 none/low/high/max 中适用于模型的值

# thinking=False
extra_body = {"thinking": {"type": "disabled"}}
reasoning_effort = "none"
```

最终参数应以所选模型当前官方文档为准；思考模式下不应发送被官方文档声明为不生效的采样参数。所有模型 ID 与参数应通过启动配置统一管理，不要在业务代码中散落硬编码。

### 5.4 联网搜索

**实现决策：本版本采用 DeepSeek Chat Completions + 应用侧 Search/Fetch，不使用 Responses API 内置 `web_search` 工具。** 原因是本版保留现有 `delta.reasoning_content` / `delta.content` 的 Chat Completions 流式适配器；DeepSeek 当前文档中的内置 `web_search` 属于 Responses API 能力，且官方兼容性表注明 `deepseek-flash` 会忽略 Responses API 的 `web_search` 工具。

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-W01 | `web_search=true` 时，后端先调用 DeepSeek Search API 获取搜索结果，再将受控长度的来源资料作为上下文交给 Chat Completions 模型 | P0 | 不把 `{"type":"web_search"}` 伪装成 Chat Completions 原生工具；集成测试能验证实际搜索请求与结果注入 |
| FR-W02 | 搜索 query 由当前用户问题生成，必须限制在 Search API 允许的 1–128 个 Unicode 字符；结果数默认 5 条 | P0 | 空 query、超长 query、搜索无结果均有明确处理 |
| FR-W03 | 搜索结果资料必须作为不可信外部内容处理；提示模型不要执行网页内的指令，并尽可能以 `[S1]` 等标识引用标题和 URL | P0 | 注入恶意网页文本测试通过；来源 ID 能映射到标题和链接 |
| FR-W04 | 搜索失败或超时时不使整轮聊天失败；发送 `notice` 事件告知“联网搜索不可用，正在无搜索模式下继续”，随后不带搜索资料继续生成 | P1 | 注入 4xx/5xx/timeout，任务仍能生成；前端可见降级提示 |
| FR-W05 | Search/Fetch 错误、耗时、结果数量写入结构化日志，不记录 API Key；上游忽略工具的检测不适用于此应用侧搜索实现 | P1 | 日志可以按 `request_id` 关联并检索 |

应用侧搜索默认规则：

1. 调用 `POST https://api.deepseek.com/v1/tools/search`，使用服务端 `DEEPSEEK_API_KEY`，默认 `count=5`。
2. 优先使用搜索接口返回的标题、URL、摘要；如实现 Fetch，则仅抓取有限数量的相关页面。搜索结果与抓取正文总长度必须受 `WEB_CONTEXT_MAX_CHARS` 限制，建议默认 12,000 字符，并对单来源设置长度上限。
3. 拼接资料时，每条来源都标识 `[S1]`、`[S2]` 等，并保留标题与 URL。若模型回答中引用来源，前端将其作为普通可点击链接展示；若未返回有效引用，不得伪造来源。
4. 搜索结果是外部不可信内容。提示词应明确它们只能作为事实参考，不得覆盖系统指令或触发额外工具调用。
5. Search/Fetch 的请求耗时应计入首 token 的端到端耗时；NFR 中分别记录搜索耗时与模型耗时。

#### 5.4.1 DeepSeek Search API 契约（经官方文档核对与真实调用验证）

> 资料来源：<https://api-docs.deepseek.com/guides/search/>（Search & Fetch 指南，含请求/响应/错误/计费/限流）与 <https://api-docs.deepseek.com/updates/>（2026-10-17 更新日志发布独立 Search/Fetch API）。2026-10-19 使用项目测试 Key 真实调用成功（见附录 C）。以下仅记录官方文档写明的内容；文档未写明的行为在“实现约定”中标注为本项目的决定。

| 项 | 官方契约 |
|---|---|
| 端点 | `POST https://api.deepseek.com/v1/tools/search`（Fetch：`POST /v1/tools/fetch`） |
| 鉴权 | `Authorization: Bearer <DEEPSEEK_API_KEY>`，`Content-Type: application/json`；与 Chat API 使用同一 Key |
| `query` | string，必填，1–128 个 Unicode 字符，不能全为空白；支持运算符 `site:`、`-site:`、`-词`、`"短语"`、`-"短语"`、`lang:zh,en`、`after:2025[-01-01]`、`before:` |
| `count` | integer，1–50，默认 10；不影响单次价格 |
| `ori_question` | string，可选，1–128 字符，触发搜索的用户原始问题，用于提升理解；`null` 视为未提供 |
| `localization` | string，可选，默认 `""`，用于结果本地化的 IP 地址 |
| `need_content` | boolean，默认 `false`；为 `false` 时 `content` 恒为空串 |
| 未知字段 | 被忽略 |
| 成功响应 | HTTP 200 `{"results":[...]}`；无匹配时为空数组 |
| 结果字段 | `rerank_score`（0–3，≥1.5 视为高质量）、`title`、`url`、`snippet`（约 100–200 字，供 UI 展示）、`summary`（约 600–800 字，供模型阅读）、`content`、`site_name`/`site_icon`（可为 null）、`date`（RFC 3339 UTC 或 null）、`authority`（0–4，-1 未知，≥3 高权威） |
| 错误格式 | OpenAI 兼容 `{"error":{"message","type","param":null,"code"}}`；400 参数非法、401 Key 无效、402 余额不足、403（Fetch 安全策略拒绝）、404（Fetch 无可用内容）、408 读取请求体超时、413 请求体过大、429 超出 QPS、500 内部错误、504 下游超时；官方建议 408/429/5xx 等待后重试，402 充值后重试，其他 4xx 不改请求不重试 |
| 计费 | 按成功请求计费：Search 与 Fetch 各 $0.75 / 1,000 次；HTTP 200 且结果非空才计费，失败、空结果、超时不计费；经 DeepSeek Harness / Claude Code / Codex 的服务端工具调用在 2026-11-01 00:00（北京时间）前免费 |
| 限流 | 账户级（与使用哪个 Key 无关），Search 与 Fetch 各 5 QPS，相互独立且与模型并发限制分开；超限返回 429；Fetch 另有按域名限流 |

实现约定（本项目决定，MUST）：

1. 请求体固定为 `{"query": <make_query(当前问题)>, "count": WEB_SEARCH_COUNT, "need_content": false, "ori_question": <当前问题前 128 字符>}`；`make_query` 将空白折叠为单空格后截断到 128 个 Unicode 字符；结果为空串时不调用接口，直接按降级处理（`SEARCH_EMPTY_QUERY`）。
2. 模型上下文使用 `summary`（为空时依次回退 `snippet`、`content`），单条截断到 `WEB_SOURCE_MAX_CHARS`（默认 2,500），总长不超过 `WEB_CONTEXT_MAX_CHARS`（默认 12,000）；UI 来源卡片使用 `title/url/snippet/site_name/site_icon/date`。
3. 本版**不调用 Fetch API**（Search 的 `summary` 已面向模型，且 Fetch 会额外增加首 token 延迟和计费）；后续如启用，必须遵守 Fetch 的域名级限流。
4. 本版对 Search 不做自动重试（避免放大首 token 延迟和 QPS）；任何非 200、超时（`SEARCH_TIMEOUT_SECONDS`，默认 15s）、JSON 结构异常都转为 `notice{code:SEARCH_UNAVAILABLE}`，空结果转为 `notice{code:SEARCH_NO_RESULTS}`，随后无搜索继续生成（FR-W04）。
5. 搜索成功时，在 `start` 之后、首个 `delta` 之前发布可回放的 `sources` 事件（§8.3），并把同一列表写入 assistant 消息的 `sources`，使刷新与其他窗口都能看到来源卡片；前端只把正文中存在于 `sources` 的 `[Sn]` 渲染为链接，不存在的编号原样显示（不伪造来源）。
6. 账户级 5 QPS 是共享上限：多副本部署时总搜索速率不得超过 5 QPS。本版不实现全局令牌桶（记为已知限制），超限时按 429 降级处理并在日志中记录 `status=429`。
7. `SEARCH_PROVIDER=deepseek|disabled|fake`：`disabled` 时 `web_search=true` 的请求一律降级并发送 `notice`；`fake` 仅用于测试。

### 5.5 SSE 订阅与断线续传（核心）

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-E01 | `GET /api/sessions/{sid}/stream` 返回 `text/event-stream`；可选 `after=<stream_id>` | P0 | Content-Type、缓存及代理头正确 |
| FR-E02 | 回放与实时读取由单一 `XREAD BLOCK` 循环实现 | P0 | 代码中没有“读完整个历史后再另行订阅”的竞态窗口 |
| FR-E03 | 显式传入的 `after` 和 `Last-Event-ID` 必须校验格式；两者同时存在时，按数值比较取较新游标 | P0 | 非法游标返回 400；不进行字符串大小比较 |
| FR-E04 | 每 15 秒无业务事件时发送 SSE `ping` 事件；ping 不写入 Redis Stream、不带 `id`、不更新游标 | P0 | 空闲连接可维持；客户端可识别 ping，但重连游标不会因此前进 |
| FR-E05 | 客户端断开后尽快终止读取循环与关联任务 | P0 | 通过 `request.is_disconnected()` 或等价机制检测；无残留协程 |
| FR-E06 | 每个可回放业务事件必须带 `id:<stream_id>`，数据为 JSON | P0 | 前端可恢复事件游标 |
| FR-E07 | 支持 `Last-Event-ID` 原生重连；当前连接的回放与后续实时事件按 Stream ID 严格递增 | P0 | 自动重连与显式刷新测试通过 |
| FR-E08 | 新页面加载时，以数据库历史作为已完成消息来源；如存在活动任务，依据活动 run 元数据创建临时消息，并从 `start_event_id` 后回放后续事件 | P0 | 不依赖其他标签页的游标；刷新后可重建当前生成中的完整已输出片段 |
| FR-E09 | 若 `after` 缺省且无 `Last-Event-ID`，服务端优先从活动任务的 `start_event_id` 开始；无活动任务时，从服务端记录的 `last_event_id` 开始实时监听 | P0 | 初次打开不会漏掉“读取状态后、建立 SSE 前”发生的事件 |

**游标与多标签页规则（MUST）**：

- `sessions.last_event_id` 是服务端观察到的会话 Stream 游标，不是浏览器的确认游标。
- 禁止把每个会话的客户端游标存进跨标签页共享的 `localStorage`。如果需要保存标签页自己的连接游标，必须用标签页隔离的 `sessionStorage`；但页面刷新后因消息状态未持久化，重建活动消息仍应从活动任务的 `start_event_id` 回放。
- 正常 EventSource 自动重连时使用浏览器传来的 `Last-Event-ID`；硬刷新/新标签页不得盲目复用其他窗口的游标。
- 历史接口与 SSE 可能并发返回同一条已完成消息。前端必须按 `message_id` / `request_id` 做合并去重，不得直接无条件 append。
- 心跳不得改变业务事件游标。

### 5.6 多实例广播与 Stream 生命周期

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-D01 | 同一会话所有订阅者（可跨实例）读取同一 Redis Stream，业务事件顺序一致 | P0 | 双实例测试事件序列一致，最终 UI 内容一致 |
| FR-D02 | 某个 API 实例的订阅者增减，不影响其他实例的投递 | P0 | 关闭一个窗口或实例不影响其他窗口继续读取已保留事件 |
| FR-D03 | 活动生成期间不按固定长度裁剪 Stream；新一轮生成开始前，在前一轮终态消息已落库后，将旧事件裁剪至保留最近一个终态事件 | P0 | 超长回答的活动 delta 不被裁剪；新一轮开始前历史仍可从 DB 读取 |
| FR-D04 | 任务进入终态后，Stream 设置 24 小时 TTL；活动任务期间不得设置会提前过期的 TTL | P1 | Stream 不无限增长；活动生成不因 TTL 消失 |
| FR-D05 | 当 Redis Stream 因 TTL 过期时，已完成内容仍可由数据库历史读取；如果任务活动状态与 Redis 状态不一致，后端必须按任务状态检测并报错，不能静默返回伪完整内容 | P1 | 过期后重开会话能显示历史；进行中任务不会假装完整 |

说明：Stream 的生命周期按“会话内当前/最近一轮生成”管理。前一轮完成后，其完整消息已在数据库中，因此在下一轮启动前可裁剪旧增量；活动轮次的所有 delta 必须保留到该轮结束或任务被判定中断。不要将 `MAXLEN ~ 5000` 用作活动任务的无条件裁剪策略。

### 5.7 中断与故障

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-F01 | 前端通过 SSE `open/error`、重连状态与 ping 判断连接状态；60 秒内无法恢复连接时显示 `interrupted` 连接提示并持续提供手动重连入口 | P1 | 网络恢复后可重连；连接断开不会自动认定生成任务已死亡 |
| FR-F02 | 后端以 worker 心跳/租约识别异常退出；心跳过期后将任务标记为 `interrupted`，终态事件可回放 | P1 | kill worker 后监控器在规定时间内转为终态并发布 `GENERATION_INTERRUPTED` |
| FR-F03 | `error` 事件必须携带 `code`、安全的 `message`、`request_id` | P0 | 前端停止 streaming 状态并显示可理解错误；不得向前端暴露 API Key 或内部堆栈 |
| FR-F04 | Search API 异常通过非终态 `notice` 事件通知；搜索降级本身不终止对话 | P1 | 用户可以区分“搜索不可用”和“模型生成失败” |
| FR-F05 | 上游 `finish_reason` 为 `insufficient_system_resource` 或 `aborted` 时，run 记为 `failed`（`UPSTREAM_ERROR`），已输出的 delta 保留在 Stream 中供回放，不写 assistant 消息；`length`、`content_filter` 视为正常结束（`done` 携带真实 `finish_reason`，前端提示“回答被截断/被过滤”） | P1 | T16b 通过 |
| FR-F06 | 进程收到 SIGTERM 正常关闭时，等待 `SHUTDOWN_GRACE_SECONDS` 让进行中的 run 结束；仍未结束的 run 以条件更新标记为 `interrupted` 并发布终态，而不是等待 45 秒租约过期 | P1 | 滚动更新时无“永远生成中”的会话 |

### 5.8 多窗口实时同步（P0）

目标：同一部署下任意窗口（同浏览器多标签、不同浏览器、不同设备）看到的会话列表与会话消息一致且实时更新，无需刷新；后端多副本、无粘性时同样成立。

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-Y01 | 提供全局 SSE `GET /api/events`（协议见 §8.4），推送会话列表变更：`session.created`、`session.updated`、`session.deleted` | P0 | T26 |
| FR-Y02 | 每个会话变更在**数据库事务提交后**发布到 `events:global`（Redis Stream；内存模式为进程内等价实现），data 携带变更后的完整 `Session` 对象（删除时为 `{id,version}`） | P0 | 事件中的数据与随后 `GET /api/sessions/{sid}` 一致 |
| FR-Y03 | 会话变更时 `version` 单调 +1（创建为 1；删除事件携带删除前 `version+1`）；客户端按 `id` 只接受 `version` 大于本地的事件，删除后对该 `id` 记墓碑，忽略之后到达的旧事件 | P0 | 乱序/重复事件注入单测通过（`sessionsReducer`） |
| FR-Y04 | 全局流使用近似裁剪 `MAXLEN ~ GLOBAL_STREAM_MAXLEN`（默认 2000）；它只用于短期重连补发，**不是**列表的事实来源 | P0 | — |
| FR-Y05 | 连接建立时服务端先发送控制事件 `ready{cursor}`；客户端收到后重新拉取会话列表（第一页），再应用此后到达的事件（按 version 合并，因此与列表快照交叠无害） | P0 | 断线重连后列表与服务端一致（T27） |
| FR-Y06 | 客户端以 `Last-Event-ID` 重连且该 ID 早于全局流保留窗口时，服务端发送 `resync{scope:"global"}`，客户端全量重拉列表 | P0 | T27 |
| FR-Y07 | 消息级同步复用会话流：`start` 事件携带 `user_message`，使打开同一会话的其他窗口立即显示该用户消息与随后的流式回复；`done` 后各窗口自行拉取历史收敛到数据库终态 | P0 | T07、UI e2e“窗口 A 实时看到窗口 B 发送的消息” |
| FR-Y08 | 当前打开的会话被其他窗口删除时，本窗口退出该会话并回到空白新对话状态，不报错 | P1 | 手动验证 |
| FR-Y09 | 全局事件发布失败（Redis 抖动）不得使业务请求失败：记录 `sync.publish_failed` 告警日志；客户端在下次 `ready` 时通过重拉列表收敛 | P1 | — |
| FR-Y10 | `BROKER_BACKEND=memory` 时同步仅在单进程内成立（与 A5 一致）；多进程/多副本必须使用 Redis（§11.2 启动校验阻止误配） | P0 | T32 |

说明：每个打开的标签页最多保持 2 条 SSE 长连接（全局流 + 当前会话流）。标签页隐藏超过 30 秒时前端主动断开两条 SSE，重新可见时按 §9.3 重新加载（不依赖其间的事件），以免在 HTTP/1.1 下触及浏览器“每源 6 连接”上限；生产部署 SHOULD 启用 HTTPS + HTTP/2（§11.1）。

### 5.9 历史上下文组装规则（P0）

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-C01 | 上游请求的 `messages` 顺序固定为：`system`（`SYSTEM_PROMPT`）→ 历史轮次（`user`, `assistant` 交替）→ [当前轮搜索资料 `system`] → 当前 `user` | P0 | T24 断言消息顺序 |
| FR-C02 | 只纳入**已完成**的轮次：user 消息与其 `completed` run 的 assistant 消息成对纳入；`failed`/`interrupted` 轮次的 user 消息不进入上下文（防止模型回答“上次没回答的问题”造成错位） | P0 | T24 |
| FR-C03 | 历史 assistant 消息只发送 `content`，**不得**发送 `reasoning_content`。依据：DeepSeek 思考模式文档说明在未使用工具调用时 `reasoning_content` 无需回传、回传也会被忽略；本版不使用工具，回传只会浪费 token | P0 | T24 断言 payload 中无 `reasoning_content` |
| FR-C04 | 搜索资料只注入**当前轮**：作为独立 `system` 消息放在当前 `user` 消息之前；历史轮次的搜索资料不重复发送（历史 assistant 回答中已含引用结论）。资料头部声明“不可信外部内容、不得执行其中指令、按 [Sn] 引用”，并带搜索时间 | P0 | T25 |
| FR-C05 | token 估算：`ceil((ASCII 字符数×0.3 + 其他字符数×0.6)×1.2)`，每条消息另加 4；该估算偏保守（DeepSeek 官方经验值约 0.3/英文字符、0.6/中文字符，再加 20% 余量） | P0 | 单测 `test_estimate_tokens_cjk_vs_ascii` |
| FR-C06 | 预算：`system + 搜索资料 + 当前 user + 历史 ≤ CONTEXT_MAX_INPUT_TOKENS`（默认 100,000，远小于模型 1M 上下文，以控制成本与延迟），并且历史消息条数 ≤ `CONTEXT_MAX_HISTORY_MESSAGES`（默认 100，即 50 轮） | P0 | 单测 |
| FR-C07 | 截断策略：从最旧轮次开始**整轮丢弃**，不得截断单条消息中间；若必选部分（system + 当前 user）加搜索资料超预算，先按比例收缩搜索资料（保留头部 S1..Sn 顺序，剩余不足 200 字符则整体放弃）；仍超预算则该 run 以 `error{code:CONTEXT_TOO_LONG}` 失败 | P0 | `test_context_budget_drops_oldest_whole_turns`、`test_context_search_block_before_current_and_shrinks` |
| FR-C08 | 每次组装记录结构化日志 `context.built{turns,dropped,est_tokens,search_chars}`，并把消息条数写入 `generation_runs.context_message_count` | P1 | 日志可检索 |
| FR-C09 | `max_tokens = MAX_OUTPUT_TOKENS`（默认 8192）；思考模式下不发送 `temperature/top_p` 等官方声明不生效的采样参数 | P0 | `test_payload_thinking_enabled` |

### 5.10 自动标题（P1）

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-N01 | 触发：会话的某个 run **正常完成**（`done` 已发布）且此时 `title_source=default`；通常即首轮完成后。失败/中断轮次不触发；之后的轮次因 `title_source≠default` 不再触发 | P1 | T28 |
| FR-N02 | 异步：在终态发布之后以后台任务执行，绝不阻塞或延迟 `done`；失败不影响会话状态 | P1 | T28：`done` 时间不受标题耗时影响 |
| FR-N03 | 模型：`DEEPSEEK_TITLE_MODEL`（默认 `deepseek-flash`），`thinking.type=disabled`，`max_tokens=40`，`temperature=0.3`，非流式；输入为首条 user 与首条 assistant 各前 1,500 字符；超时 `TITLE_TIMEOUT_SECONDS`（默认 20s） | P1 | 单测检查请求参数 |
| FR-N04 | 长度与清洗：提示词要求“与用户同语言、中文 ≤12 字/英文 ≤6 词、无引号/句号/前缀”；结果取第一行、去掉“标题：”前缀、首尾引号/标点/Markdown 符号，折叠空白，最终硬截断到 `TITLE_MAX_CHARS`（默认 24） | P1 | `test_clean_title` |
| FR-N05 | 兜底：模型调用失败、超时或清洗后为空时，使用首条用户消息折叠空白后截断（超长时截到 `TITLE_MAX_CHARS-1` 并加 `…`），同样记为 `title_source=auto` | P1 | T29 |
| FR-N06 | 并发安全：写入使用条件更新 `WHERE title_source='default'`；若用户在生成期间已手动重命名（`title_source=user`），自动标题被丢弃 | P1 | T29 |
| FR-N07 | 广播：写入成功后发布 `session.updated`（version+1），所有窗口列表与标题栏同步更新 | P1 | T28、UI e2e“窗口 B 看到标题” |
| FR-N08 | 开关：`TITLE_GENERATION_ENABLED=false` 时不生成，保持默认标题；指标 `title_generation_total{outcome=ok|fallback|skipped|error}` | P2 | — |

### 5.11 会话搜索（P0）

| ID | 需求 | 优先级 | 验收标准 |
|---|---|---|---|
| FR-Q01 | `GET /api/sessions?q=<关键词>&limit=`：`q` 去首尾空白后 1..100 字符，否则 400；匹配“会话标题 **或** 任一消息的 `content`（user/assistant）”包含 `q` 的会话；**不**搜索 `reasoning_content` 与搜索来源 | P0 | T30、`test_search_ignores_reasoning` |
| FR-Q02 | 匹配语义为大小写不敏感的子串匹配；`%`、`_`、`\` 按字面量处理（转义后使用 `LIKE ... ESCAPE '\'`），不支持通配与分词 | P0 | T31 |
| FR-Q03 | 排序为 `updated_at` 倒序；搜索模式最多返回 `limit` 条，不分页（`next_cursor=null`） | P0 | — |
| FR-Q04 | 每条结果带 `match`：标题命中为 `{field:"title",snippet:title}`；否则为最早命中的消息 `{field:"message",snippet,message_id,role}`，`snippet` 为命中位置前后约 60 字符的片段（必要时加 `…`） | P0 | T30 |
| FR-Q05 | 前端搜索框输入防抖 250ms；结果高亮命中词；清空后恢复普通列表；搜索期间收到全局事件时，列表在退出搜索后按普通模式重拉 | P1 | UI e2e“侧栏搜索” |

数据库语义差异（MUST 文档化并测试）：

| 方面 | PostgreSQL | SQLite |
|---|---|---|
| 实现 | `ILIKE '%q%' ESCAPE '\'` | SQLAlchemy 将 `ilike` 编译为 `lower(x) LIKE lower(y) ESCAPE '\'` |
| 大小写折叠 | 按数据库排序规则对 Unicode 字母折叠（如 `É/é`、`Ä/ä`） | `lower()` 只折叠 ASCII 字母；非 ASCII 字母大小写敏感 |
| 中文 | 子串匹配，无分词 | 同左 |
| 性能 | 顺序扫描 + `lower(title)` 索引；数据量增长后应引入 `pg_trgm` GIN 索引或全文检索（P2） | 顺序扫描；仅用于本地开发 |

测试只对 ASCII 大小写不敏感与中文子串做跨库一致性断言；非 ASCII 大小写差异作为已知差异记录，不做断言。

---

## 6. 非功能需求

| ID | 需求 | 指标/说明 |
|---|---|---|
| NFR-01 | 应用端首增量延迟 | 从上游 chunk 到 Redis `XADD` 的应用处理开销 P95 < 300ms；不包含上游推理及 Search/Fetch 耗时 |
| NFR-02 | SSE 追加延迟 | 从 Redis `XADD` 到浏览器事件处理的 P95 < 200ms，测试环境需记录网络条件 |
| NFR-03 | 单实例并发 SSE 连接 | ≥ 500；必须用 500 连接测试验证，不以 50 并发测试代替 |
| NFR-04 | 发送接口响应 | P99 < 200ms，不含模型生成；在连接池和测试硬件固定的条件下评估 |
| NFR-05 | 可用性 | 单实例故障不影响已完成轮次读取；已发布事件仍可从 Redis 回放；进行中任务最终被标记为中断，不自动重跑 |
| NFR-06 | 日志 | JSON 结构化，至少含 `trace_id/session_id/request_id/event`；不得记录 API Key、完整鉴权头或未经脱敏的敏感信息 |
| NFR-07 | 指标 | 活跃 SSE 连接数、Stream 长度、活动任务数、任务终态耗时、搜索耗时、生成耗时、error 率（Prometheus，P1） |
| NFR-08 | 安全 | API Key 仅存服务端环境变量/密钥管理服务；CORS 使用精确白名单；用户 `content` 长度 ≤ 32,000 字符；无鉴权的版本仅供内网/演示使用 |
| NFR-09 | 上下文预算 | 配置最大输出 token 与历史上下文预算；超过预算时按明确策略截断或返回可理解错误，不得静默构造超出模型上下文的请求 |
| NFR-10 | 超时与关闭 | 配置上游连接/读取超时、应用关闭时等待时间和后台任务清理策略；超时后按失败/中断路径完成状态收敛 |
| NFR-11 | 响应式 | 断点：`≥1024px` 桌面（侧栏常驻 260px，可折叠）；`768–1023px` 平板（侧栏可折叠）；`<768px` 移动（侧栏为抽屉，遮罩关闭）；触控目标 ≥ 40×40px；视口 320–1920px 无横向滚动；详见 §9.7 |
| NFR-12 | 同步时延 | 会话列表变更从 DB 提交到其他窗口列表更新 P95 < 2s（局域网、无粘性双副本）；消息事件同 NFR-02 |
| NFR-13 | 搜索性能 | 1 万会话 / 10 万消息规模下 PostgreSQL 搜索 P95 < 500ms 为目标；超出时引入 `pg_trgm`（P2，本版未压测） |
| NFR-14 | 可部署性 | `docker compose` 一键启动；所有服务带健康检查；后端镜像非 root 运行；配置全部来自环境变量，镜像与仓库中不含任何密钥 |

**性能口径**：成功标准中的“首 token 3 秒内可见”按集成基准测试记录端到端 P50/P95；必须同时记录 Search/Fetch、上游首 token、Redis 发布、浏览器处理各阶段耗时，不得仅用应用内部耗时宣称端到端达标。

---

## 7. 数据模型

以下为 PostgreSQL 的逻辑模型（带 `--` 注释说明列/索引用途）。SQLite 本地开发使用**同一条 Alembic 迁移链**，在迁移脚本内按方言分支处理差异（时间类型、函数索引、批量改表 `render_as_batch`），并在两种数据库上分别运行 `alembic upgrade head` / `downgrade base` 与全部集成测试；不得假定同一段 DDL 可在两种数据库中完全通用（见 ADR-014）。

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,                       -- ≤200 字符
  title_source TEXT NOT NULL DEFAULT 'default'
    CHECK (title_source IN ('default','auto','user')),          --
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,           -- 最近活动时间，列表排序键
  last_event_id TEXT NOT NULL DEFAULT '0-0',
  status TEXT NOT NULL DEFAULT 'idle' CHECK (status IN ('idle', 'generating')),
  active_request_id TEXT,
  next_sequence_no BIGINT NOT NULL DEFAULT 1,
  version BIGINT NOT NULL DEFAULT 1,         -- 每次变更 +1，全局同步按版本合并
  CHECK ((status = 'generating') = (active_request_id IS NOT NULL))
);

CREATE TABLE generation_runs (
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  request_id TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('accepted', 'running', 'completed', 'failed', 'interrupted')),
  thinking BOOLEAN NOT NULL DEFAULT FALSE,
  web_search BOOLEAN NOT NULL DEFAULT FALSE,
  model TEXT,                                -- 实际使用的模型 ID
  owner_token TEXT,                          -- 当前持有者，条件更新的互斥后备
  instance_id TEXT,                          -- 运行实例，便于排障
  user_message_id TEXT,
  assistant_message_id TEXT,
  start_event_id TEXT,
  terminal_event_id TEXT,
  terminal_event_published BOOLEAN NOT NULL DEFAULT FALSE,
  finish_reason TEXT,
  error_code TEXT,
  error_message TEXT,
  usage_json TEXT,                           -- 上游 usage（prompt/completion/reasoning tokens）
  context_message_count INTEGER,             -- 本轮发给上游的消息条数
  created_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (session_id, request_id)
);

CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  sequence_no BIGINT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL DEFAULT '',
  reasoning_content TEXT NOT NULL DEFAULT '',
  sources_json TEXT NOT NULL DEFAULT '[]',   -- 联网搜索来源（assistant）
  finish_reason TEXT,                        -- assistant 的上游结束原因
  request_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  UNIQUE (session_id, sequence_no),
  UNIQUE (session_id, request_id, role)
);

CREATE INDEX idx_messages_session_sequence ON messages(session_id, sequence_no);
CREATE INDEX idx_runs_status_heartbeat ON generation_runs(status, heartbeat_at);
CREATE INDEX idx_runs_unpublished ON generation_runs(terminal_event_published, status);  -- 终态修复扫描
CREATE INDEX ix_sessions_updated_at ON sessions(updated_at);                              -- 列表排序
CREATE INDEX ix_sessions_title_lower ON sessions(lower(title));                           -- 搜索
```

### 7.1 数据约束

- 每个被接受的生成任务恰好关联一条 user message；正常完成时最多一条 assistant message。失败/中断轮次不创建空 assistant message，而是通过 `generation_runs` 状态与前端错误提示呈现。
- `sequence_no` 由数据库事务内分配；必须保证同一会话内唯一递增。分配顺序与写入消息顺序应在会话级互斥或行锁保护下执行。
- 对同一请求重试不得插入第二条消息；消息和 run 的数据库唯一约束是最终防线。
- `sessions.status=generating` 时，`active_request_id` 必须非空；`status=idle` 时必须为空。删除会话前需确认没有活动/待发布终态的任务。
- `last_event_id` 只允许写入已确认存在的 Stream ID；它可以短暂落后，但禁止提前于 Stream 中已经存在的事件。
- 数据库任务状态是任务是否完成、失败或中断的事实来源；Redis 终态标记用于发布幂等，不替代数据库任务记录。
- `version` 只能通过 `version = version + 1` 的 UPDATE 推进，禁止应用层读出再写回；全局事件中的 `version` 取自同一事务提交后的行。
- `title_source` 状态机：`default → auto`（自动标题，条件更新）、`default|auto → user`（手动重命名）；`user` 不会再被自动覆盖。
- worker 对 run 的每次状态写入都带 `WHERE owner_token = :mine AND status IN (...)` 条件；返回 0 行即视为租约已失去（`LeaseLost`），立即停止写入。

### 7.2 Redis 键

| Key | 类型 | 说明 |
|---|---|---|
| `stream:{sid}` | Stream | 会话事件流；当前活动任务事件全量保留至终态；终态后 TTL 默认 24 小时 |
| `lock:gen:{sid}` | String | 生成互斥锁，值为 `owner_token`，默认 TTL 30 秒，worker 每 10 秒续租 |
| `dedupe:{sid}:{request_id}` | String | 可选的快速幂等缓存；TTL 不能替代数据库唯一约束 |
| `job:{sid}:{request_id}:heartbeat` | String | 可选任务心跳；短 TTL；以数据库 `heartbeat_at` 辅助核验 |
| `terminal:{sid}:{request_id}` | String | 终态发布幂等标记，值存已发布终态事件的 Stream ID；写标记与 `XADD` 必须用同一 Lua 脚本/原子操作完成 |
| `events:global` | Stream | 全局会话变更事件流，`XADD MAXLEN ~ 2000`；不设 TTL；仅用于短期补发 |

说明：键名中的 `{sid}` 为字面量花括号包裹的会话 ID，例如 `stream:{3f2a…}`、`lock:gen:{3f2a…}`、`terminal:{3f2a…}:{rid}`。在 Redis Cluster 中花括号是 hash tag，同一会话的 Stream、锁与终态标记落在同一槽位，保证终态 Lua 脚本可在集群中执行。本版实现未使用 `dedupe:` 与 `job:…:heartbeat` 可选键（以数据库唯一约束与 `heartbeat_at` 为准）。

### 7.3 `last_event_id` 更新规则（MUST）

- 发布 `start`、`delta`、`notice`、`done`、`error` 后，更新内存中的本轮游标；数据库 `last_event_id` 在关键状态转换时立即更新，生成期间可按每 2 秒或每 100 个业务事件节流更新。
- 终态事件 ID 以 Redis 实际返回值为准；禁止应用层自增。
- 终态发布脚本若检测到 `terminal:{sid}:{request_id}` 已存在，应返回原来的终态 Stream ID，不得重复 `XADD`。
- 终态发布成功与数据库将 `terminal_event_published` 置为 true 之间发生崩溃时，重试应取回已记录的 Stream ID，并安全完成数据库状态收尾。
- 各实例均可运行轻量终态修复扫描器，查找 `status` 已终结但 `terminal_event_published=false` 的 run；通过原子发布脚本补发/确认事件后，再完成会话状态收尾。

---

## 8. API 契约

### 8.1 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/sessions` | 创建会话；可选请求体 `{title?}`；返回 201 `Session` |
| GET | `/api/sessions` | 会话列表 `?limit=1..200(默认50)&cursor=<next_cursor>`，按 `updated_at` 倒序，返回 `{items: Session[], next_cursor: string\|null}`；带 `q=` 时为搜索模式（§5.11），`items[].match` 非空 |
| GET | `/api/sessions/{sid}` | 单个会话 `Session` |
| PATCH | `/api/sessions/{sid}` | 重命名 `{title}`，返回 `Session`；`title_source=user` |
| DELETE | `/api/sessions/{sid}` | 删除空闲会话，返回 204；生成中返回 409 `SESSION_BUSY` |
| GET | `/api/sessions/{sid}/messages` | 历史消息（包装）`{items: Message[], oldest_sequence_no, newest_sequence_no, has_more_before}`；查询参数 `after_sequence_no` / `before_sequence_no` / `limit`(1..200)；单独 `limit` 时返回最近 N 条；按 `sequence_no` 升序 |
| POST | `/api/sessions/{sid}/messages` | 发送消息；接受后返回 202 `{accepted:true,session_id,request_id,status,user_message_id}` |
| GET | `/api/sessions/{sid}/runs/{request_id}` | 获取单次任务状态、错误码、时间信息及 `start_event_id`（不返回 owner_token 等内部信息） |
| GET | `/api/sessions/{sid}/active-run` | 无活动任务时返回 `{active:false,last_event_id}`；有活动任务时返回 `{active:true,request_id,status,start_event_id,thinking,web_search,user_message_id,last_event_id}` |
| GET | `/api/sessions/{sid}/stream` | 会话 SSE；可选 `after` 参数，支持 `Last-Event-ID` |
| GET | `/api/events` | 全局 SSE（§8.4）；可选 `after`，支持 `Last-Event-ID` |
| GET | `/api/config` | 前端所需的非敏感配置：`{model,search_provider,title_generation,max_content_chars,broker,db}` |
| GET | `/api/health` | 进程存活检查（不访问依赖） |
| GET | `/api/ready` | Redis、数据库及必需依赖的就绪检查；失败返回 503 并列出失败项 |
| GET | `/api/metrics` | Prometheus 指标；**不得**经公网入口暴露（nginx 返回 403），仅供内网抓取 |

`Session` 对象：

```json
{
  "id": "uuid", "title": "新对话", "title_source": "default|auto|user",
  "created_at": "ISO-8601", "updated_at": "ISO-8601",
  "last_event_id": "0-0", "status": "idle|generating", "active_request_id": null,
  "version": 3,
  "match": null
}
```

`Message` 对象：`{id,session_id,sequence_no,role,content,reasoning_content,sources:[{id:"S1",title,url,snippet,site_name,site_icon,date}],finish_reason,request_id,created_at}`。

`/api/health` 不应因瞬时数据库故障而被误用为唯一就绪信号；负载均衡应使用 `/api/ready` 决定是否向该实例发送新请求。SSE 连接较多时，健康检查不得阻塞在连接清理或慢查询中。

消息请求体：

```json
{
  "content": "用户输入，1..32000 字符",
  "thinking": false,
  "web_search": false,
  "request_id": "UUID"
}
```

字段规则：`content` 去除首尾空白后不能为空；`thinking`、`web_search` 必须为 boolean；`request_id` 必须是合法 UUID；额外字段默认拒绝（例如 Pydantic `extra="forbid"`）。请求 hash 应对标准化后的内容及选项生成，不能把同一幂等键用于不同消息。

### 8.2 错误码

| HTTP | code | 场景 |
|---|---|---|
| 400 | `INVALID_REQUEST` | 参数非法、游标格式错误、字段类型错误 |
| 404 | `SESSION_NOT_FOUND` | 会话不存在 |
| 404 | `RUN_NOT_FOUND` | 任务不存在 |
| 409 | `GENERATION_IN_PROGRESS` | 同会话已有活动或终态发布未完成的生成任务 |
| 409 | `SESSION_BUSY` | 删除生成中的会话 |
| 409 | `IDEMPOTENCY_KEY_REUSED` | 相同 `(session_id, request_id)` 对应不同请求内容 |
| 422→400 | `INVALID_REQUEST` | Pydantic 校验错误统一映射为 400 + 统一错误体，不返回 FastAPI 默认 422 结构 |
| 429 | `RATE_LIMITED` | 仅在未来实际启用限流后返回；当前版本不承诺限流能力 |
| 502 | `UPSTREAM_ERROR` | DeepSeek Chat Completions 调用失败 |
| 503 | `DEPENDENCY_UNAVAILABLE` | Redis/数据库等必需依赖不可用，无法安全接受任务 |

SSE `error`/`notice` 事件中的 code（不对应 HTTP 状态）：`UPSTREAM_ERROR`、`GENERATION_INTERRUPTED`、`CONTEXT_TOO_LONG`、`SEARCH_UNAVAILABLE`、`SEARCH_NO_RESULTS`。

统一 HTTP 错误体：

```json
{
  "error": {
    "code": "GENERATION_IN_PROGRESS",
    "message": "当前会话已有生成任务，请等待其结束。",
    "request_id": "可选"
  }
}
```

### 8.3 SSE 事件协议

| event | data JSON | 是否有 Stream ID | 说明 |
|---|---|---:|---|
| `start` | `{"request_id":"...","status":"running","thinking":bool,"web_search":bool,"user_message":Message}` | 是 | 当前生成任务开始；前端按 `user_message` 合并用户消息（多窗口实时显示），并创建以 `request_id` 为键的临时 assistant 消息 |
| `sources` | `{"request_id":"...","sources":[Source]}` | 是 | 联网搜索成功后、首个 delta 前发布；前端显示来源卡片 |
| `delta` | `{"request_id":"...","kind":"reasoning\|content","text":"..."}` | 是 | 增量片段 |
| `notice` | `{"request_id":"...","code":"SEARCH_UNAVAILABLE","message":"..."}` | 是 | 非终态提示，例如搜索降级 |
| `done` | `{"request_id":"...","message_id":"...","finish_reason":"stop","message":Message}` | 是 | 正常终态；完成消息已先落库；`message` 为完整 MessageOut 供前端晋升临时气泡 |
| `error` | `{"request_id":"...","code":"UPSTREAM_ERROR","message":"..."}` | 是 | 失败/中断终态，与 `done` 互斥 |
| `ping` | `{"at":"ISO-8601 时间"}` 或空数据 | 否 | 仅心跳，不进入 Stream，不推进 `Last-Event-ID` |
| `resync` | `{"scope":"session","reason":"CURSOR_TOO_OLD\|STREAM_MISSING",...}` | 否 | 连接建立时的控制事件：游标早于保留窗口（已裁剪/过期）或活动 run 的 Stream 丢失；前端重新执行 §9.3 加载流程，`STREAM_MISSING` 时把活动消息标记为异常（FR-D05） |

连接建立时服务端首先发送 `retry: 3000`，建议浏览器 3 秒后重连。

业务事件样例：

```text
id: 1700000000000-1
event: delta
data: {"request_id":"...","kind":"content","text":"你好"}

```

约束：

- 业务事件顺序必须与 Redis `XADD` 顺序一致。
- SSE `id` 是 Redis Stream ID；`event_key` 如有需要放在 data 内，不能替代 `id`。
- `done` 事件到达时，数据库中对应 assistant 消息必须已经可读。
- `error` 与 `done` 是逻辑互斥的终态。对一个 run，前端按 `request_id` 只允许一个终态；后端发布器使用原子幂等逻辑确保 Redis 中不重复写入终态。
- `finish_reason` 应保留上游真实结束原因，例如 `stop`、`length`、`tool_calls` 等；需要额外工具处理的中间步骤不能误报为最终 `done`。
- `delta.kind=reasoning` 仅在 `thinking=true` 时出现；`thinking=false` 时即使上游意外返回 `reasoning_content` 也丢弃并记录告警日志（FR-T02）。

### 8.4 全局 SSE 协议

`GET /api/events`，响应头同会话流（`text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`）。

| event | data JSON | 有 id | 说明 |
|---|---|---:|---|
| `ready` | `{"cursor":"<stream_id>"}` | 否 | 连接建立后第一条；客户端据此重拉会话列表第一页，再合并之后的事件 |
| `resync` | `{"scope":"global","reason":"CURSOR_TOO_OLD"}` | 否 | `Last-Event-ID` 早于 `events:global` 保留窗口 |
| `session.created` | `{"session":Session}` | 是 | 新会话 |
| `session.updated` | `{"session":Session}` | 是 | 标题（手动/自动）、状态、`updated_at` 变化 |
| `session.deleted` | `{"id":"...","version":n}` | 是 | 删除 |
| `ping` | `{"at":"..."}` | 否 | 每 `PING_INTERVAL_SECONDS` 无事件时发送 |

游标规则：缺省 `after` 与 `Last-Event-ID` 时从全局流当前末尾开始（只接收新事件，历史以列表接口为准）；其余游标校验、数值比较、单一 `XREAD BLOCK` 循环规则与会话流完全相同（FR-E02/E03/E07、C1/C2/C7）。

客户端合并规则（MUST，实现于 `sessionsReducer`）：

```text
onReady:            重拉 GET /api/sessions（第一页），与本地状态按 version 合并（快照中 version 更高者胜）
onCreated/Updated:  若 tombstone[id] 存在 → 忽略；若 local[id].version >= ev.version → 忽略；否则 upsert
onDeleted:          记录 tombstone[id]=version；从列表移除
排序:               按 updated_at 倒序；按“今天/昨天/7 天内/30 天内/更早”分组展示
```

---

## 9. 前端规格

### 9.1 结构

```text
<App>                         useGlobalSync(/api/events) · useTheme · useMediaQuery · usePageActive
 ├─ <Sidebar>                 新建 · 搜索框(防抖) · 日期分组列表 · 重命名/删除 · 主题切换（移动端为抽屉）
 └─ <ChatView>                useSessionStream(/api/sessions/{sid}/stream) + chatReducer
     ├─ 顶栏                  侧栏开关 · 会话标题 · 连接状态
     ├─ 空状态 Hero           “有什么可以帮忙的？” + 居中输入框
     ├─ <MessageItem>×N       用户气泡 / 助手全宽 Markdown；<ReasoningBlock> 默认折叠；来源卡片；notice；错误条
     └─ <Composer>            自适应高度输入框 · 思考/联网药丸开关 · 发送按钮
```

技术栈：React 19.2 + Vite 8 + TypeScript 5.9（strict）；`react-markdown` + `remark-gfm` + `rehype-highlight` 渲染 Markdown 与代码高亮；`lucide-react` 图标；原生 `EventSource` 自行封装（未使用 `@reactuses/core`，以便精确控制重连与 `resync`）。状态用 `useState`/`useReducer`，不引入额外状态库；样式为原生 CSS 变量（无 UI 框架）。

### 9.2 状态与持久化

| 状态 | 持久化 | 说明 |
|---|---|---|
| `sessionId` | `localStorage.activeSessionId` | 可用于恢复当前选中会话 |
| 标签页连接游标 | `sessionStorage` 或组件状态 | 不得放在跨标签页共享的 `localStorage` 中作为唯一游标 |
| `messages` | 不强制持久化 | 页面重载后先读取数据库历史，再按活动 run 回放临时消息 |
| `streaming` | 否 | 由事件与 run 状态驱动 |
| `connectionStatus` | 否 | `connecting/open/reconnecting/interrupted` |
| 会话列表 | 否 | 由列表接口 + 全局 SSE 维护，含 `version` 与删除墓碑 |
| 主题 | `localStorage.theme` | `system/light/dark` |
| 思考/联网开关 | 组件状态 | 随每次发送提交；默认均为关闭 |

### 9.3 加载与恢复流程（MUST）

> 步骤 1、2 的顺序为“**先 active-run 后 history**”。若先读 history 再读 active-run，任务恰好在两次请求之间完成时，history 不含终态消息而 active-run 返回无活动任务，前端会从 `last_event_id` 起订阅，从而漏掉该轮回答。本版顺序下，若 active-run 返回活动任务，即使随后完成，history 也会包含终态消息，或从 `start_event_id` 回放能补齐 `done`。

1. 查询活动 run（响应同时给出服务端 `last_event_id`）。
2. 读取会话历史并以 `sequence_no` 排序。
3. 若存在活动 run，先根据 `active-run` 响应创建以 `request_id` 为键的临时消息，再从其 `start_event_id` 后订阅，回放后续 delta/notice/终态事件。由于 `after` 是 exclusive，`start` 事件本身不会再次投递；若无活动 run，则从服务端当前 `last_event_id` 开始监听新事件。
4. `GET history` 与 SSE 可能有交叠，必须按 `message_id`、`request_id` 和 `sequence_no` 去重合并。
5. 页面刷新导致临时消息内存丢失时，不得仅从最后保存的游标恢复；必须从活动 run 的 `start_event_id` 回放，重建当前临时消息。
6. 浏览器 EventSource 自动重连时，使用 `Last-Event-ID` 继续已打开连接的读取位置。
7. 收到 `resync` 时关闭连接并从步骤 1 重新执行。
8. 标签页隐藏超过 30 秒时释放会话流与全局流；重新可见时从步骤 1 重新执行（全局流收到 `ready` 后重拉列表）。

### 9.4 消息合并算法（MUST）

```text
onHistoryLoaded(history):
  按 message_id 合并；最终按 sequence_no 排序
  不清除已发现的 active run 元数据

onStart(request_id, id):
  若数据库历史中不存在此 request_id 的 assistant 消息，创建/复用该 request_id 对应的临时 assistant 消息
  记录当前 active request_id
  更新游标

onDelta(request_id, kind, text, id):
  若该 Stream ID 已应用，丢弃
  若 request_id 对应临时消息不存在，创建临时消息
  追加到 content 或 reasoning_content
  在成功应用事件后更新标签页游标与 lastAppliedId

onNotice(request_id, code, message, id):
  显示非终态提示；不停止 streaming
  更新游标

onSources(request_id, sources, id):
  写入临时 assistant 消息的 sources；更新游标

onStart 补充:
  若 data.user_message 存在，按 message_id / request_id 合并进消息列表（替换本窗口的乐观临时用户消息）

onDone(request_id, id, message_id):
  重新拉取历史，或获取终态消息
  按 request_id/message_id 替换临时消息，禁止临时消息和终态消息并存
  清除该 run 的 streaming 状态并更新游标

onError(request_id, code, message, id):
  将对应临时消息标记为失败/中断或保留已输出内容并显示错误条
  结束该 run 的 streaming 状态并更新游标
```

**合并补充**：history 快照可能比 SSE 更旧（例如 SSE 已投递 `start.user_message` 后 history 才返回）。合并时保留本地已有、且 `sequence_no` 大于快照最大值的已持久化消息，不得被旧快照删除；本窗口发送时先插入乐观用户消息（临时 id），以 `request_id` 与服务器消息对齐替换。从“无会话”状态发送首条消息时，先创建会话再发送，乐观消息在切换到新会话 id 时保留。

**去重规则**：比较 Stream ID 时，拆分为 `milliseconds-sequence` 两部分并按整数比较。对同一有序 Stream，维护 `lastAppliedId` 即可丢弃 `id <= lastAppliedId` 的重复事件；若未来支持多流合并，则必须改为按 `(stream_id, event_key)` 去重。不可直接按字符串比较。

### 9.5 交互

- Enter 发送，Shift+Enter 换行。
- 同会话生成期间禁用发送按钮；后端仍必须执行互斥检查，不能依赖前端禁用实现并发控制。
- 切换会话时关闭当前 SSE 连接并清理当前视图状态；**关闭 SSE 只代表取消订阅，不代表取消后端生成任务**。
- 连接状态展示：`connecting / open / reconnecting / interrupted`。
- 区分“连接中断”和“生成任务中断”：SSE 连接断开先进入 `reconnecting`；只有后端 run 状态进入 `interrupted` 或连接长期无法恢复时才显示相应提示。
- `error` 事件需要将对应任务从 streaming 状态移出，不能让输入框永久禁用。
- Enter 发送仅限桌面物理键盘；触控设备上 Enter 换行、点击按钮发送；输入法组合输入（`isComposing`）期间 Enter 不发送。
- 发送返回 409 `GENERATION_IN_PROGRESS` 且是本窗口刚结束的上一轮尚在终态收尾时，前端以**同一 `request_id`** 按 400ms×n 退避最多重试 5 次（幂等保证不会重复生成）；网络错误或 5xx 以同一 `request_id` 再重试 2 次；其他错误显示提示。
- 代码块提供“复制”按钮；助手消息提供“复制”（全文）；链接新窗口打开（`rel="noopener noreferrer"`）。

### 9.6 视觉与交互风格（ChatGPT/Grok 风格）

| ID | 要求 | 验收 |
|---|---|---|
| UI-01 | 两栏布局：左侧会话侧栏（新建、搜索、按“今天/昨天/前 7 天/前 30 天/更早”分组的列表），右侧对话区；对话区内容列居中、最大宽度 768px | 截图 `desktop-chat-*.png` |
| UI-02 | 空会话显示居中问候语与大输入框（Hero），发送首条消息后输入框固定在底部 | `desktop-empty-dark.png` |
| UI-03 | 用户消息为右对齐圆角气泡（最大 80% 宽）；助手消息无气泡、全宽 Markdown 排版，带小头像 | 截图 |
| UI-04 | 输入框为圆角“胶囊”卡片：自适应高度（最多约 8 行）、左下为“思考”“联网搜索”药丸开关（激活高亮），右下为圆形发送按钮；接近 32,000 字符上限时显示计数 | 截图 |
| UI-05 | 思考过程为可折叠块，默认收起；生成中标题显示“思考中…”，结束后显示“已深度思考”；展开后以弱化色展示 | `desktop-thinking-expanded-dark.png` |
| UI-06 | 搜索来源以横向滚动卡片展示（站点图标、站点名、标题）；正文 `[S1]` 渲染为上标链接 | 截图 |
| UI-07 | 流式输出时显示光标/脉冲指示；连接状态非 `open` 时在顶栏显示“重新连接中…/连接已中断（重连）” | — |
| UI-08 | 深色/浅色/跟随系统三种主题，首屏前通过内联脚本应用主题，避免闪烁；配色中性灰阶，强调色单一 | `*-dark.png`/`*-light.png` |
| UI-09 | 侧栏项悬停显示“重命名/删除”；生成中的会话显示进行中标识；当前会话高亮；搜索结果显示命中片段并高亮关键词 | `desktop-search-light.png` |
| UI-10 | 无障碍：按钮有 `aria-label`；焦点可见；`prefers-reduced-motion` 时关闭动画 | 代码审查 |

### 9.7 响应式与移动端

| ID | 要求 | 验收 |
|---|---|---|
| RS-01 | 断点：`≥1024px` 侧栏常驻 260px（可手动收起）；`768–1023px` 侧栏 240px；`<768px` 侧栏变为左侧抽屉（宽 `min(84vw,320px)`），带半透明遮罩，点击遮罩/选择会话/新建后自动关闭 | `mobile-drawer-dark.png` |
| RS-02 | 视口元信息：`width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content`；使用 `env(safe-area-inset-*)` 适配刘海与 Home 指示条 | 代码审查 |
| RS-03 | 高度使用 `visualViewport` 计算的 `--vvh`，软键盘弹出时输入框保持可见、页面不整体滚动 | 真机抽测（本版未做真机） |
| RS-04 | 输入框字号 ≥16px，避免 iOS 聚焦自动缩放；触控目标 ≥40px | 代码审查 |
| RS-05 | 390×844 下：无横向滚动（`scrollWidth ≤ innerWidth`）；代码块与表格在自身容器内横向滚动；长 URL 断行 | UI e2e `no_horizontal_overflow` |
| RS-06 | 移动端隐藏助手头像与页脚免责声明，以节省空间；顶栏标题居中 | `mobile-chat-dark.png` |

### 9.8 多窗口同步（前端）

- `useGlobalSync` 在应用级别保持一条 `/api/events` 连接；`ready` → 重拉列表；`session.*` → `sessionsReducer` 按 version 合并（§8.4）。
- 当前会话标题、状态（生成中）取自同步后的列表数据，因此其他窗口重命名或自动标题会立即反映到顶栏。
- 当前会话被删除（任何窗口）时退出到新对话空状态。
- 打开同一会话的多个窗口各自持有独立会话流与独立游标（不共享，符合 §5.5）；本窗口发送的消息在其他窗口经 `start.user_message` 实时出现。

---

## 10. 并发与时序规范（实现红线）

| ID | 规范 | 原因 |
|---|---|---|
| C1 | 禁止“先 replay 再 subscribe”两阶段实现 | 两阶段间存在丢事件窗口 |
| C2 | `XREAD` 的 `last_id` 必须更新为上一批最后一条 Stream ID，不得持续使用固定 `after` | 避免重复读取或事件循环卡住 |
| C3 | 正常完成时先提交 assistant 消息与任务终态，再发布 `done` | 确保客户端收到 `done` 后历史已可读 |
| C4 | 锁释放必须在终态发布成功并完成状态收尾之后；用 token 校验安全释放 | 防止新任务取得的锁被旧任务误删 |
| C5 | 幂等查询先于锁竞争；锁竞争先于插入用户消息 | 重试不重复生成，冲突请求不留下用户消息 |
| C6 | 任何实例不得缓存“本实例专属”的共享会话游标或任务状态 | 多实例一致性 |
| C7 | Stream ID 比较必须按两个整数进行 | 字符串比较会在 `-9` 与 `-10` 等边界出错 |
| C8 | 活动任务不允许固定长度裁剪；完成后数据库消息为历史事实来源 | 避免长回答 delta 被裁剪后无法恢复 |
| C9 | 终态发布必须幂等；数据库终态已提交但 Redis 终态未发布时必须可重试修复 | 处理跨 PostgreSQL/Redis 的非原子写入窗口 |
| C10 | 心跳只判断连接；任务存活由 worker heartbeat/lease 判断 | 防止长思考期误报和 ping 掩盖 worker 已死 |
| C11 | 获取/续租/释放锁均校验 `owner_token`，并有过期任务扫描器 | 避免锁过期后两个 worker 同时运行 |
| C12 | 删除会话前确认无活动 run 和未完成的终态发布 | 防止后台任务向已删除会话写入 |
| C13 | 依赖方向 `api → services → repositories/infra`；SQL 只在 repositories；Redis 客户端只在 infra | 可替换实现、可测试（§4.3） |
| C14 | 全局事件只在数据库提交**之后**发布；事件携带提交后的 `version`；客户端按 version 合并 | 避免其他窗口看到未提交或倒退的数据 |
| C15 | worker 每次写 run 都带 `owner_token` 条件；0 行更新即停止 | 锁意外过期后的双写防护 |
| C16 | 历史上下文不得包含 `reasoning_content`、失败/中断轮次与历史搜索资料；截断以整轮为单位 | §5.9 |
| C17 | 任何日志、错误体、`/api/config`、`describe()` 输出不得包含 API Key 或数据库密码；日志格式化器对 `sk-…`/`Bearer …` 模式二次脱敏 | NFR-06/08 |

---

## 11. 部署与配置

所有配置均来自环境变量（`pydantic-settings`，大小写不敏感；本地可用 `backend/.env`，已被 `.gitignore` 忽略）。`APP_PROFILE` 提供预设，显式设置的单项优先于预设。

**Profile 预设**

| `APP_PROFILE` | DB_BACKEND | BROKER_BACKEND | 日志格式 | 用途 |
|---|---|---|---|---|
| `dev`（默认） | `sqlite` | `memory` | text | 本地单进程开发，无需 Redis/PostgreSQL |
| `test` | `sqlite` | `memory` | text | 自动化测试（测试套件可显式切换到 postgres+redis） |
| `prod` | `postgres` | `redis` | json | 生产/多副本；禁止 memory 与 sqlite（§11.2） |

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
| `CONTEXT_MAX_INPUT_TOKENS` ★ | `100000` | 输入预算（§5.9） |
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

Compose 专用（根目录 `.env`，见 `.env.example`）：`HTTP_PORT`（默认 8080）、`BACKEND_REPLICAS`（默认 2）、`POSTGRES_PASSWORD`（必填）。

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
| `docker-compose.yml` | `postgres:17-alpine`（`pg_isready` 健康检查，命名卷）、`redis:7-alpine`（AOF，`redis-cli ping` 健康检查）、一次性 `migrate`（`alembic upgrade head`，`service_completed_successfully` 门控）、`backend`（`deploy.replicas: ${BACKEND_REPLICAS:-2}`，`APP_PROFILE=prod`，`EXPECTED_REPLICAS` 同步副本数，`/api/ready` 健康检查，`stop_grace_period: 20s`）、`nginx`（前端静态 + 反向代理，端口 `${HTTP_PORT:-8080}`，依赖 backend healthy） |
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
- Python 版本：`requires-python >= 3.12`，镜像与开发环境使用 3.13。
- 代码质量：`ruff check` 与 `ruff format --check` 零告警为合入条件（行宽 110）。

---

## 12. 测试规格

### 12.1 功能用例

| 用例 | 步骤 | 期望 |
|---|---|---|
| T01 基础流式 | 发送消息 | delta 持续出现；收到 done 时完整 assistant 消息已可从历史接口读取，且与 delta 拼接一致；若历史提前读到终态消息，前端必须按 request_id 合并而不是重复显示 |
| T02 思考模式开启 | `thinking=true` 连续两轮 | reasoning 折叠展示；请求显式启用思考；多轮不报参数错误 |
| T03 思考模式关闭 | `thinking=false` 连续两轮 | 请求显式禁用思考；不出现非预期 reasoning 区块 |
| T04 联网搜索成功 | `web_search=true` 发送问题 | 调用 Search API；上下文来源有标题和 URL；回答不伪造引用 |
| T05 联网搜索失败 | 模拟 Search API timeout/5xx | 收到 notice，继续无搜索生成，最终可 done |
| T06 刷新续传 | 生成中刷新页面 | 从活动 run 起点重放并重建临时消息，最终不丢不重 |
| T07 双窗口广播 | 同会话两个窗口同时打开 | 两边最终内容及消息顺序一致；游标相互独立 |
| T08 多实例广播 | 两个窗口经 LB 落在不同实例 | 同 T07，无会话粘性仍可工作 |
| T09 幂等并发 | 同 session 同 request_id 同时 POST 两次 | 仅一个任务、一条 user message，不重复调用模型 |
| T10 幂等键冲突 | 同 request_id 改变 content 或 options 再提交 | 返回 409 `IDEMPOTENCY_KEY_REUSED` |
| T11 同会话互斥 | 第一轮生成中提交另一个 request_id | 返回 409，不新增用户消息 |
| T12 旧游标重连 | 使用旧 `after` 重连 | 不重复显示消息；活动任务缺口按任务起点恢复或触发明确恢复流程 |
| T13 游标格式 | `after=abc` 或无效负数 | 400 `INVALID_REQUEST` |
| T14 Stream ID 边界 | 比较 `...-9` 与 `...-10` | 按数值顺序比较，游标不倒退 |
| T15 心跳 | 空闲 30 秒 | 至少收到一次 ping；ping 不改变 Last-Event-ID |
| T16 上游失败 | 注入 DeepSeek 502/timeout | run 变为 failed，收到 error，UI 复位，不写空 assistant 消息 |
| T17 实例崩溃 | 生成中 kill worker 所在实例 | 已 XADD 的 delta 可回放；心跳过期后转为 interrupted 并发布 error；不自动重跑 |
| T18 锁过期与安全释放 | 模拟旧 worker 锁过期、新 owner 获锁，旧 worker尝试释放 | 旧 owner 不能续租或删除新 owner 的锁 |
| T19 终态发布窗口 | DB 终态提交后、XADD 前杀进程；XADD 后、DB 标记 published 前杀进程 | 修复扫描器最终发布/确认唯一逻辑终态；不重复 XADD |
| T20 长回答裁剪 | 生成超过 5,000 个 delta 的回答 | 活动期间事件不被裁剪；完成后新任务启动才裁剪旧事件 |
| T21 删除会话 | 空闲会话删除；生成中会话删除 | 空闲成功；生成中 409；无任务继续写入已删除会话 |
| T22 历史与 SSE 竞态 | history 返回期间收到 done，或 done 后 history 已含 assistant | 按 request_id/message_id 合并，不重复显示两条 assistant |
| T23 数据库依赖故障 | DB 不可用时 POST | 不接受任务，不返回伪 202；readiness 失败 |
| T23b Redis 依赖故障 | Redis 不可用时 POST | 503 `DEPENDENCY_UNAVAILABLE`，不新增用户消息；readiness 失败 |
| T24 上下文组装 | 构造含已完成、失败、中断轮次及 reasoning 的历史 | 上游 messages 为 system→已完成轮次→[搜索 system]→当前 user；无 `reasoning_content`；失败/中断轮次的 user 不出现 |
| T25 预算截断 | 设置较小 `CONTEXT_MAX_INPUT_TOKENS`；超长搜索资料 | 最旧整轮丢弃；搜索资料按比例收缩；必选部分仍超限 → `CONTEXT_TOO_LONG` |
| T26 全局同步 | 打开 `/api/events`；创建、重命名、发送首轮（自动标题）、删除 | 依次收到 `session.created`、`session.updated`（含 rename、生成开始/结束、标题）、`session.deleted`，version 单调递增，数据与 GET 一致 |
| T27 全局重连 | 用 `Last-Event-ID` 重连；用早于保留窗口的 ID 重连 | 前者补发缺失事件且不重复；后者收到 `resync{scope:global}`；连接首条为 `ready` |
| T28 自动标题 | 首轮完成；第二轮完成 | 首轮后标题变为模型输出（清洗后 ≤24 字），`title_source=auto`，广播 `session.updated`；第二轮不再调用标题模型；`done` 不等待标题 |
| T29 标题兜底与保护 | 标题模型失败；生成期间手动重命名 | 失败时使用首条消息截断；手动重命名后自动标题不覆盖 |
| T30 会话搜索 | 标题命中、user 正文命中、assistant 正文命中、仅 reasoning 命中 | 前三者返回且 `match` 正确、片段含关键词；仅 reasoning 命中不返回 |
| T31 搜索转义与大小写 | 搜索 `%`、`_`、ASCII 大小写混合、中文子串 | `%`/`_` 按字面匹配；ASCII 大小写不敏感；中文子串命中；PG 与 SQLite 一致 |
| T32 配置开关与启动校验 | 组合 §11.2 V1–V7 | 违规组合启动失败且信息明确；dev/prod 预设切换正确；`describe()` 不含密钥 |
| T33 分页 | 创建 > limit 个会话后翻页 | `updated_at` 倒序，无重复/遗漏 |
| T34 思考关闭防泄漏 | `thinking=false` 而上游仍返回 reasoning | 不产生 `kind=reasoning` 事件，不保存 reasoning |
| T35 UI 端到端 | 无头 Chrome：两个窗口 A/B 同时打开；B 联网+思考发送；A 观察；侧栏搜索；手机视口 | B 有来源卡片、思考块、代码块；A 实时出现 B 的用户消息与回复，两边消息一致；B 看到自动标题；搜索命中；手机抽屉可用，无横向滚动 |
| T36 Docker 部署 | `./deploy.sh`；连续 `/api/ready`；跨副本全局流/会话流；真实 e2e；kill 生成中副本 | 见 §11.3 验收 ①–⑤ |
| T37 真实 DeepSeek | `scripts/e2e_live.py` | 流式回复、思考开/关、联网来源与引用、多轮记忆、自动标题、会话搜索均通过 |

**自动化测试映射**（`backend/tests/`，同一套用例分别在 `sqlite-memory` 与 `postgres-redis` 两种模式下运行）：

| 文件 | 覆盖 |
|---|---|
| `test_unit.py` | T13/T14 游标、T24/T25 上下文、T32 配置、搜索 query/资料块、标题清洗、payload 参数 |
| `test_brokers.py` | Broker 契约（两实现）：exclusive 游标、阻塞唤醒、T18 锁 owner 安全、终态幂等、`trim_before`、`publish_capped` |
| `test_generation.py` | T01–T06、T06b、T09–T11b、T16/T16b、T20、T21、T34、多轮记忆 |
| `test_sse_sync.py` | T07、T12、T13、T15、SSE 头与断开清理、T26–T31、T33、请求校验、health/ready/metrics |
| `test_recovery.py` | T17（真实子进程 SIGKILL）、T17-lite、T19a/b、锁过期仍忙、T23/T23b、T08（两个服务实例共享 Redis） |
| `frontend/src/lib/chatReducer.test.ts` | §9.4 合并算法、T22 历史与 SSE 竞态、旧快照不删新消息 |
| `frontend/scripts/ui-e2e.mjs` | T35 + 截图 |
| `backend/scripts/e2e_live.py` | T37（dev 与 Docker 两种部署各跑一次） |

### 12.2 并发/性能

- **500 并发 SSE × 5 分钟**：目标单实例连接数 ≥500；报告成功率、断连率、CPU、内存、事件延迟和 FD 使用。初期可先用 50 连接做冒烟测试，但不能替代 500 连接验收。
- `done` 与订阅建立竞态压测至少 1,000 次：已提交完整消息不丢；终态最终可见；UI 不出现重复 assistant 消息。
- 多实例负载测试：至少 2 个 API 实例、无粘性 LB、同时包含生成请求与跨实例 SSE 订阅。
- 幂等压力测试：同一 request_id 并发提交，验证数据库唯一约束、锁竞争与返回值。
- 首 token P50/P95、Search/Fetch 耗时、上游首 token、XADD→浏览器事件延迟分别采集，不合并成一个不可诊断的指标。
- 进程 kill 测试覆盖 worker 在以下阶段退出：接受请求后、start 事件写入后、delta 写入期间、数据库终态提交后、终态 XADD 后。
- 全局流：100 个并发 `/api/events` 订阅者 + 每秒 10 次会话变更，事件全部到达且 version 有序（P1，本版未执行）。

---

## 13. 验收清单（Definition of Done）

状态标记：`[x]` 已通过并有证据；`[ ]` 未完成（原因见 §14 / 附录 B）。证据位于 `docs/`。

- [x] 所有 P0 功能需求通过；思考开启和关闭均通过。
- [x] §10 的 C1–C17 红线通过代码审查，重点检查游标比较、用户消息插入顺序、锁 owner 校验和终态修复。
- [x] §12.1 的 T01–T04、T06–T16、T19、T22、T23 通过（两种模式）。
- [x] §12.1 的 T17/T18/T20/T21 通过（T17 含真实 SIGKILL 子进程与 Docker `kill` 副本两种方式）。
- [x] 双实例 + 无粘性 LB 下 T08 通过（pytest 双实例 + Docker nginx 双副本）。
- [ ] 500 并发 SSE 压测达到 NFR-03 —— **未执行**，不对该指标作通过声明。
- [x] 结构化日志含 `trace_id`、`session_id`、`request_id`；错误可检索且不泄露凭据。
- [x] 部署配置、数据库迁移、环境变量说明、故障处理与已知限制文档齐全（本文 §11、README）。
- [x] README 明确注明：无鉴权、任务不自动重跑、内存 Broker 仅本地单进程、Stream 仅用于有限期回放。
- [x] R1 uv：`uv.lock` 已提交，镜像使用 `uv sync --frozen`。
- [x] R2 分层：§4.3 验收命令通过。
- [x] R3 上下文：T24/T25/T34 通过。
- [x] R4 多窗口同步：T26/T27 + UI e2e 窗口 A/B + Docker 跨副本测试通过。
- [x] R5 会话搜索：T30/T31 在 PG 与 SQLite 下通过。
- [x] R6 自动标题：T28/T29 通过；真实模型生成标题（例：“记住安琪的身份”）。
- [x] R7/R8 UI 与响应式：1440×900 与 390×844 截图（`docs/screenshots/`）人工检查通过；UI e2e 无横向溢出。
- [ ] R8 真机（iOS Safari / Android Chrome）软键盘行为 —— 未验证，仅无头 Chrome 模拟。
- [x] R9 Search API：契约与官方文档逐项核对（§5.4.1），真实调用成功。
- [x] R10 Docker：§11.3 验收 ①–⑤ 全部通过。
- [x] R11 配置开关：T32 通过；Docker 中 prod profile 正确选择 postgres + redis。
- [x] 前端 `typecheck`、`lint`、`test`、`build` 全部通过；后端 `ruff check` / `ruff format --check` 通过。
- [x] 仓库与镜像中不含 API Key（`git grep` 检查）。

---

## 14. 已知限制与后续演进

| 限制 | 影响 | 当前处理 / 后续演进 |
|---|---|---|
| 进行中 delta 仅在 Redis Stream 保留，实例崩溃后不自动续跑 | 用户可看到已输出片段，但任务需重新发起 | 当前通过任务状态表、心跳监控与 `interrupted` 终态明确告知；自动接管重跑列为 P2 |
| assistant 正文仅在终态一次性落库 | 生成期间历史接口不包含完整临时正文 | 活动任务依赖 Stream 回放；若需要跨 Redis 丢失仍不丢内容，后续再设计增量落库或持久化日志 |
| Stream 设有限期 TTL | 长时间离线的客户端不能依靠旧 Stream 重放历史轮次 | 完成轮次以数据库历史为准；新任务开始时可裁剪旧 Stream |
| 无用户鉴权 | 无法区分会话所有者，存在数据越权风险 | 仅内网/演示；公开部署前增加认证、会话归属校验、CSRF/CORS 策略和授权测试 |
| 搜索失败时降级为无搜索生成 | 当前回答可能不含实时信息 | 以 `notice` 明示降级；后续可接入备用搜索服务 |
| 搜索 QPS 无全局限流 | 账户级 5 QPS；多副本高并发时可能收到 429 并降级为无搜索 | 后续在 Redis 中实现全局令牌桶 |
| 不调用 Fetch API | 回答依赖 Search 摘要（`summary` 约 600–800 字），细节可能不足 | 后续对高分结果（`rerank_score ≥ 1.5`）有选择地 Fetch |
| HTTP/1.1 连接数 | 每标签页 2 条 SSE；同浏览器多标签时可能触及 6 连接上限 | 生产启用 HTTP/2；前端隐藏 30 秒释放 SSE；后续可用 SharedWorker/BroadcastChannel 复用全局流 |
| 搜索为子串扫描 | 大数据量下变慢；无分词/相关度 | 引入 `pg_trgm` / 全文检索 |
| 自动标题仅首轮 | 话题变化后标题不更新 | 用户可手动重命名 |
| 启动多实例校验基于声明 | 未声明 `EXPECTED_REPLICAS` 却手动启动多份内存模式实例时无法检测 | Compose 自动设置；后续可通过 Redis 心跳注册检测 |
| 中断轮次不保存部分正文 | 刷新可从 Stream 看到部分输出，Stream 过期后只剩错误提示 | 本版约定不落库部分正文；后续可在中断时落库部分内容 |
| 进程内后台任务不是持久化队列 | 实例退出会使模型调用中断 | 当前任务监控并标记中断；若需要自动接管，应引入持久化任务队列/独立 worker 并重新设计幂等和上游重试语义 |
| 无用户主动取消 | 关闭窗口不会停止上游生成 | 后续增加取消接口、worker 取消信号和终态事件规则 |

---

## 15. 决策记录（ADR 摘要）

| ADR | 决策 | 理由 | 被否方案 |
|---|---|---|---|
| 001 | 事件总线用 Redis Streams | 同一结构支持可回放事件、实时读取和跨实例共享 | Pub/Sub（无历史回放）、仅进程内存（不跨实例） |
| 002 | 传输用 SSE | 单向事件足够，天然支持 Last-Event-ID，较易实现 | WebSocket（本需求不要求双向实时协议） |
| 003 | 终态正文落库；活动正文通过 Stream 回放；任务状态单独落库 | 避免每 token 更新消息表，同时具备任务故障检测和终态修复能力 | 只在 Stream 保存全部状态（缺少任务事实来源）；每 chunk UPDATE 消息行（写放大） |
| 004 | Stream ID 由 Redis 分配，客户端按数值 tuple 比较 | 保持共享事件顺序，避免字符串比较错误 | 应用自增 ID、字符串大小比较 |
| 005 | 同会话生成互斥 | 避免两次生成交叉写入一条流 | 同会话排队（本版本不实现） |
| 006 | 多实例无会话粘性 | 任意实例都可以提供同一共享 Stream 的读取 | 依赖一致性哈希/会话粘性 |
| 007 | 搜索采用 DeepSeek Search/Fetch 应用侧预处理，模型调用沿用 Chat Completions | 与本版本现有 Chat Completions 流式适配相容；避免依赖不兼容的内置工具声明 | 直接向 Chat Completions 传 `{"type":"web_search"}` |
| 008 | 任务终态状态持久化并由幂等发布器写入 Redis | 处理 DB 与 Redis 之间不存在分布式原子事务的问题 | 假定数据库提交与 XADD 可以原子完成 |
| 009 | 新标签页从活动 run 起点恢复；标签页游标不跨窗口共享 | 刷新可重建临时消息，避免其他窗口推进游标导致跳读 | 用 localStorage 保存每个会话唯一游标 |
| 010 | 列表同步使用单独的全局 Redis Stream + SSE，事件带 version | 复用已有的 Stream/SSE/游标机制；可补发；跨实例 | Redis Pub/Sub（断线丢事件）、前端轮询（延迟高、负载大）、WebSocket |
| 011 | 全局事件携带完整 Session 对象而非仅 id | 客户端无需额外请求即可更新；version 合并保证幂等 | 只发 id 再让每个窗口 GET（放大请求） |
| 012 | 上下文不回传 reasoning；搜索资料只注入当前轮、置于当前 user 之前的 system 消息 | 官方说明无工具时 reasoning 被忽略；节省 token；资料离问题最近，且历史不重复 | 把资料拼进 user 消息（污染持久化内容）、每轮重发历史资料 |
| 013 | 标题在 `done` 之后异步生成，条件更新 `title_source='default'` | 不影响首 token 与终态；避免覆盖用户命名 | 与回答同一请求生成（拖慢）、前端生成（多窗口重复） |
| 014 | SQLite 与 PG 共用一条 Alembic 链，按方言分支 | 避免两套迁移漂移；两库均测试 | 独立迁移脚本（维护成本高） |
| 015 | nginx `resolve` 动态解析 + round-robin，镜像内置前端 | 一个入口同时提供静态资源与 API；副本伸缩无需改配置 | 每副本一个固定 upstream；单独的前端容器 + 另一个 LB |
| 016 | 搜索应用侧调用独立 Search API，不用 Responses API 内置 `web_search` | 保持 Chat Completions 流式适配；可控的资料长度与来源展示；`deepseek-flash` 在 Responses API 中会忽略 web_search | Responses API 内置工具 |
| 017 | §9.3 先 active-run 后 history | 消除先 history 后 active-run 顺序下的漏消息竞态 | 先 history 后 active-run |

---

## 附录 A. 官方 API 参考

以下链接用于实现时核对请求字段和模型能力。模型及 API 能力可能更新，开发时必须再次验证，不应仅依赖本文参数示例。

1. DeepSeek Chat Completions API：<https://api-docs.deepseek.com/api/create-chat-completion/>
2. DeepSeek 思考模式：<https://api-docs.deepseek.com/guides/thinking_mode/>
3. DeepSeek Search & Fetch：<https://api-docs.deepseek.com/guides/search/>
4. DeepSeek Responses API 兼容性说明（用于理解为何本版本不采用内置 `web_search`）：<https://api-docs.deepseek.com/guides/responses_api/>
5. DeepSeek 更新日志（2026-10-17 发布独立 Search/Fetch API；`deepseek-flash` 为 DeepSeek-V4.1-Flash；旧 `deepseek-chat`/`deepseek-reasoner` 已下线）：<https://api-docs.deepseek.com/updates/>
6. DeepSeek 模型与价格：<https://api-docs.deepseek.com/quick_start/pricing/>
7. Token 用量估算：<https://api-docs.deepseek.com/quick_start/token_usage/>

---

## 附录 B. 实现决策与假设

需求方在通宵实现期间不可联系，以下为实现时自行作出、需要确认的决定（均已在正文对应位置体现）：

1. 会话列表按 `updated_at` 倒序（最近活动优先）；重命名与自动标题也会刷新 `updated_at`。
2. §9.3 加载顺序为先 active-run 后 history（ADR-017）。
3. `start` 事件携带 `user_message`；新增可回放的 `sources` 事件；新增控制事件 `ready`/`resync`（不带 id）。
4. 失败/中断轮次不进入上下文；搜索资料只用于当前轮；搜索 query = 当前问题折叠空白后截断 128 字符（不额外调用模型改写 query，以免增加延迟）。
5. 不使用 Fetch API；Search 不重试；搜索失败/无结果均降级继续。
6. 标题：`deepseek-flash`、思考关闭、`max_tokens=40`、`temperature=0.3`、≤24 字符；失败兜底为首条消息截断；只在 `title_source=default` 时生成。
7. 上游 `finish_reason ∈ {insufficient_system_resource, aborted}` 视为失败；`length`/`content_filter` 视为正常完成并在 UI 提示。
8. SIGTERM 优雅关闭时，未完成任务立即标记 `interrupted`（不等租约过期）。
9. 上下文预算默认 100K token（模型支持 1M），历史最多 100 条；token 采用保守字符估算，而非精确 tokenizer。
10. `REASONING_EFFORT` 默认 `high`。
11. 内存/SQLite 的多实例保护基于 `EXPECTED_REPLICAS`/`WEB_CONCURRENCY` 声明。
12. `/api/metrics` 仅内网可访问（nginx 403）；`X-Upstream` 调试响应头保留，生产可删除。
13. 前端默认深色/浅色跟随系统；思考与联网开关默认关闭，且不持久化。
14. 无鉴权（本版约定），不得暴露公网。

## 附录 C. DeepSeek Search API 调研与验证记录

- 调研时间：2026-10-19（北京时间）。来源：官方指南 <https://api-docs.deepseek.com/guides/search/> 与更新日志 <https://api-docs.deepseek.com/updates/>（2026-10-17 条目宣布独立 Search 与 Fetch API；中文媒体报道价格约 5 元/千次，与官方 $0.75/千次一致量级，以官方为准）。
- 契约：见 §5.4.1，全部字段、取值范围、错误码、计费与限流均摘自官方指南，未作推测性补充。
- 实测（使用测试 Key，Key 只从仓库外的环境文件（例如 `/etc/ai-chat/secrets.env` 或本机私有路径）在运行时加载）：
  - `POST /v1/tools/search {"query":"…","count":5}` 返回 200，`results` 含 `title/url/snippet/summary/site_name/site_icon/date/rerank_score/authority`，与文档一致。
  - 空 `query` 返回 400 `invalid_request_error`，与文档一致。
  - 端到端：联网问题的回答引用 `[S1]…`，来源卡片可点击（`docs/e2e-report-*.json` 中 `web_search_sources`、`web_search_cited` 为 true）。
- 未验证：429 限流行为、402 余额不足行为、Fetch API（本版未使用）。

---

**Spec 编程落地建议**：按 §5 的需求 ID 拆分任务卡；§8/§9 作为接口与 UI 契约源；§10 作为 Code Review 红线；§12 作为测试清单。实现前先建立数据库迁移、任务状态机和 Redis Broker 的契约测试，再并行开发前后端。任何偏离 §4.2、§10 的实现都必须更新本文与 ADR，不能只通过代码注释隐式改变需求。
