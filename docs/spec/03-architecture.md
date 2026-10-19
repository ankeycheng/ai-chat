> 返回 [规格总入口](../spec.md)

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
- **A10**：后端按[后端分层](#43-后端分层must)；HTTP 层不得直接访问数据库或 Redis，基础设施实现（Redis/内存 Broker、PostgreSQL/SQLite、DeepSeek/Fake LLM、Search）只能经接口注入。MUST

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
