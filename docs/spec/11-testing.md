> 返回 [规格总入口](../spec.md)

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
| T06b 默认游标续传 | 生成中订阅 `/stream`（不带 `after` / `Last-Event-ID`） | 默认从活动 run 起点续传；不重放 `start`，能收到后续 delta 并最终 done |
| T07 双窗口广播 | 同会话两个窗口同时打开 | 两边最终内容及消息顺序一致；游标相互独立 |
| T08 多实例广播 | 两个窗口经 LB 落在不同实例 | 同 T07，无会话粘性仍可工作 |
| T09 幂等并发 | 同 session 同 request_id 同时 POST 两次 | 仅一个任务、一条 user message，不重复调用模型 |
| T10 幂等键冲突 | 同 request_id 改变 content 或 options 再提交 | 返回 409 `IDEMPOTENCY_KEY_REUSED` |
| T11 同会话互斥 | 第一轮生成中提交另一个 request_id | 返回 409，不新增用户消息 |
| T11b 并行互斥 | 同会话同时提交多个不同 request_id | 仅一个 202，其余 409；历史仅一条 user+assistant |
| T12 旧游标重连 | 使用旧 `after` 重连 | 不重复显示消息；活动任务缺口按任务起点恢复或触发明确恢复流程 |
| T13 游标格式 | `after=abc` 或无效负数 | 400 `INVALID_REQUEST` |
| T14 Stream ID 边界 | 比较 `...-9` 与 `...-10` | 按数值顺序比较，游标不倒退 |
| T15 心跳 | 空闲 30 秒 | 至少收到一次 ping；ping 不改变 Last-Event-ID |
| T16 上游失败 | 注入 DeepSeek 502/timeout | run 变为 failed，收到 error，UI 复位，不写空 assistant 消息 |
| T16b 中途失败保留增量 | 上游在已输出若干 delta 后失败（含非 done 类 `finish_reason` / timeout） | 已写入 Stream 的 delta 可回放；收到 error、无 done；不写 assistant 消息 |
| T17 实例崩溃 | 生成中 kill worker 所在实例 | 已 XADD 的 delta 可回放；心跳过期后转为 interrupted 并发布 error；不自动重跑 |
| T17-lite 租约过期打断 | 构造心跳已过期的活动 run（不真实 SIGKILL） | 监控器标为 `interrupted` 并发布 `GENERATION_INTERRUPTED`；已写 delta 可回放；锁按 token 释放 |
| T18 锁过期与安全释放 | 模拟旧 worker 锁过期、新 owner 获锁，旧 worker尝试释放 | 旧 owner 不能续租或删除新 owner 的锁 |
| T19a 终态未发布 | DB 已提交终态但尚未 XADD（模拟发布窗口崩溃） | 修复扫描器补发唯一终态事件；重复扫描不重复 XADD |
| T19b 已发布未打标 | 终态已 XADD 但 DB `terminal_event_published` 未置位 | 修复扫描器确认并打标；不重复 XADD，会话 `last_event_id` 收敛 |
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
| T32 配置开关与启动校验 | 组合 [启动校验](10-deploy.md#112-启动校验) V1–V7 | 违规组合启动失败且信息明确；dev/prod 预设切换正确；`describe()` 不含密钥 |
| T33 分页 | 创建 > limit 个会话后翻页 | `updated_at` 倒序，无重复/遗漏 |
| T34 思考关闭防泄漏 | `thinking=false` 而上游仍返回 reasoning | 不产生 `kind=reasoning` 事件，不保存 reasoning |
| T35 UI 端到端 | 无头 Chrome：两个窗口 A/B 同时打开；B 联网+思考发送；A 观察；侧栏搜索；手机视口 | B 有来源卡片、思考块、代码块；A 实时出现 B 的用户消息与回复，两边消息一致；B 看到自动标题；搜索命中；手机抽屉可用，无横向滚动 |
| T36 Docker 部署 | `./deploy.sh`；连续 `/api/ready`；跨副本全局流/会话流；真实 e2e；kill 生成中副本 | 见 [Docker 一键部署](10-deploy.md#113-docker-一键部署) 验收 ①–⑤ |
| T37 真实 DeepSeek | `scripts/e2e_live.py` | 流式回复、思考开/关、联网来源与引用、多轮记忆、自动标题、会话搜索均通过 |

**自动化测试映射**（`backend/tests/`，同一套用例分别在 `sqlite-memory` 与 `postgres-redis` 两种模式下运行）：

| 文件 | 覆盖 |
|---|---|
| `test_unit.py` | T13/T14 游标、T24/T25 上下文、T32 配置、搜索 query/资料块、标题清洗、payload 参数 |
| `test_brokers.py` | Broker 契约（两实现）：exclusive 游标、阻塞唤醒、T18 锁 owner 安全、终态幂等、`trim_before`、`publish_capped` |
| `test_generation.py` | T01–T06、T06b、T09–T11b、T16/T16b、T20、T21、T34、多轮记忆 |
| `test_sse_sync.py` | T07、T12、T13、T15、SSE 头与断开清理、T26–T31、T33、请求校验、health/ready/metrics |
| `test_recovery.py` | T17（真实子进程 SIGKILL）、T17-lite、T19a/b、锁过期仍忙、T23/T23b、T08（两个服务实例共享 Redis） |
| `frontend/src/lib/chatReducer.test.ts` | [消息合并算法](08-frontend.md#94-消息合并算法must)、T22 历史与 SSE 竞态、旧快照不删新消息 |
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

## 13. 验收清单（Definition of Done）

状态标记：`[x]` 已通过并有证据；`[ ]` 未完成（原因见 [已知限制与后续演进](12-limitations-and-adrs.md#14-已知限制与后续演进) / [实现决策与假设](appendix-b-decisions.md#附录-b-实现决策与假设)）。证据位于 `docs/`。

- [x] 所有 P0 功能需求通过；思考开启和关闭均通过。
- [x] [并发与时序规范](09-concurrency.md#10-并发与时序规范实现红线) 的 C1–C17 红线通过代码审查，重点检查游标比较、用户消息插入顺序、锁 owner 校验和终态修复。
- [x] [功能用例](#121-功能用例) 的 T01–T04、T06–T16、T19a/b、T22、T23 通过（两种模式）。
- [x] [功能用例](#121-功能用例) 的 T17/T18/T20/T21 通过（T17 含真实 SIGKILL 子进程与 Docker `kill` 副本两种方式）。
- [x] 双实例 + 无粘性 LB 下 T08 通过（pytest 双实例 + Docker nginx 双副本）。
- [ ] 500 并发 SSE 压测达到 NFR-03 —— **未执行**，不对该指标作通过声明。
- [x] 结构化日志含 `trace_id`、`session_id`、`request_id`；错误可检索且不泄露凭据。
- [x] 部署配置、数据库迁移、环境变量说明、故障处理与已知限制文档齐全（本文 [部署与配置](10-deploy.md#11-部署与配置)、README）。
- [x] README 明确注明：无鉴权、任务不自动重跑、内存 Broker 仅本地单进程、Stream 仅用于有限期回放。
- [x] R1 uv：`uv.lock` 已提交，镜像使用 `uv sync --frozen`。
- [x] R2 分层：[后端分层](03-architecture.md#43-后端分层must) 验收命令通过。
- [x] R3 上下文：T24/T25/T34 通过。
- [x] R4 多窗口同步：T26/T27 + UI e2e 窗口 A/B + Docker 跨副本测试通过。
- [x] R5 会话搜索：T30/T31 在 PG 与 SQLite 下通过。
- [x] R6 自动标题：T28/T29 通过；真实模型生成标题（例：“记住安琪的身份”）。
- [x] R7/R8 UI 与响应式：1440×900 与 390×844 截图（`docs/screenshots/`）人工检查通过；UI e2e 无横向溢出。
- [ ] R8 真机（iOS Safari / Android Chrome）软键盘行为 —— 未验证，仅无头 Chrome 模拟。
- [x] R9 Search API：契约与官方文档逐项核对（[DeepSeek Search API 契约](04-features.md#541-deepseek-search-api-契约经官方文档核对与真实调用验证)），真实调用成功。
- [x] R10 Docker：[Docker 一键部署](10-deploy.md#113-docker-一键部署) 验收 ①–⑤ 全部通过。
- [x] R11 配置开关：T32 通过；Docker 中 prod profile 正确选择 postgres + redis。
- [x] 前端 `typecheck`、`lint`、`test`、`build` 全部通过；后端 `ruff check` / `ruff format --check` 通过。
- [x] 仓库与镜像中不含 API Key（`git grep` 检查）。
