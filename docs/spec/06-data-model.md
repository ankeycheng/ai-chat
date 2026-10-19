> 返回 [规格总入口](../spec.md)

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
