> 返回 [规格总入口](../spec.md)

## 8. API 契约

### 8.1 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/sessions` | 创建会话；可选请求体 `{title?}`；返回 201 `Session` |
| GET | `/api/sessions` | 会话列表 `?limit=1..200(默认50)&cursor=<next_cursor>`，按 `updated_at` 倒序，返回 `{items: Session[], next_cursor: string\|null}`；带 `q=` 时为搜索模式（[会话搜索](04-features.md#511-会话搜索p0)），`items[].match` 非空 |
| GET | `/api/sessions/{sid}` | 单个会话 `Session` |
| PATCH | `/api/sessions/{sid}` | 重命名 `{title}`，返回 `Session`；`title_source=user` |
| DELETE | `/api/sessions/{sid}` | 删除空闲会话，返回 204；生成中返回 409 `SESSION_BUSY` |
| GET | `/api/sessions/{sid}/messages` | 历史消息（包装）`{items: Message[], oldest_sequence_no, newest_sequence_no, has_more_before}`；查询参数 `after_sequence_no` / `before_sequence_no` / `limit`(1..200)；单独 `limit` 时返回最近 N 条；按 `sequence_no` 升序 |
| POST | `/api/sessions/{sid}/messages` | 发送消息；接受后返回 202 `{accepted:true,session_id,request_id,status,user_message_id}` |
| GET | `/api/sessions/{sid}/runs/{request_id}` | 获取单次任务状态、错误码、时间信息及 `start_event_id`（不返回 owner_token 等内部信息） |
| GET | `/api/sessions/{sid}/active-run` | 无活动任务时返回 `{active:false,last_event_id}`；有活动任务时返回 `{active:true,request_id,status,start_event_id,thinking,web_search,user_message_id,last_event_id}` |
| GET | `/api/sessions/{sid}/stream` | 会话 SSE；可选 `after` 参数，支持 `Last-Event-ID` |
| GET | `/api/events` | 全局 SSE（[全局 SSE 协议](#84-全局-sse-协议)）；可选 `after`，支持 `Last-Event-ID` |
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
| `resync` | `{"scope":"session","reason":"CURSOR_TOO_OLD\|STREAM_MISSING",...}` | 否 | 连接建立时的控制事件：游标早于保留窗口（已裁剪/过期）或活动 run 的 Stream 丢失；前端重新执行 [加载与恢复流程](08-frontend.md#93-加载与恢复流程must) 加载流程，`STREAM_MISSING` 时把活动消息标记为异常（FR-D05） |

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
