> 返回 [规格总入口](../spec.md)

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
