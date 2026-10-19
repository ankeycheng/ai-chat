> 返回 [规格总入口](../spec.md)

## 附录 B. 实现决策与假设

需求方在通宵实现期间不可联系，以下为实现时自行作出、需要确认的决定（均已在正文对应位置体现）：

1. 会话列表按 `updated_at` 倒序（最近活动优先）；重命名与自动标题也会刷新 `updated_at`。
2. [加载与恢复流程](08-frontend.md#93-加载与恢复流程must) 加载顺序为先 active-run 后 history（ADR-017）。
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
