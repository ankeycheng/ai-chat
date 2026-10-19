> 返回 [规格总入口](../spec.md)

## 附录 C. DeepSeek Search API 调研与验证记录

- 调研时间：2026-10-19（北京时间）。来源：官方指南 <https://api-docs.deepseek.com/guides/search/> 与更新日志 <https://api-docs.deepseek.com/updates/>（2026-10-17 条目宣布独立 Search 与 Fetch API；中文媒体报道价格约 5 元/千次，与官方 $0.75/千次一致量级，以官方为准）。
- 契约：见 [DeepSeek Search API 契约](04-features.md#541-deepseek-search-api-契约经官方文档核对与真实调用验证)，全部字段、取值范围、错误码、计费与限流均摘自官方指南，未作推测性补充。
- 实测（使用测试 Key，Key 只从仓库外的环境文件（例如 `/etc/ai-chat/secrets.env` 或本机私有路径）在运行时加载）：
  - `POST /v1/tools/search {"query":"…","count":5}` 返回 200，`results` 含 `title/url/snippet/summary/site_name/site_icon/date/rerank_score/authority`，与文档一致。
  - 空 `query` 返回 400 `invalid_request_error`，与文档一致。
  - 端到端：联网问题的回答引用 `[S1]…`，来源卡片可点击（`docs/e2e-report-*.json` 中 `web_search_sources`、`web_search_cited` 为 true）。
- 未验证：429 限流行为、402 余额不足行为、Fetch API（本版未使用）。

---

**Spec 编程落地建议**：按 [功能需求](04-features.md#5-功能需求) 的需求 ID 拆分任务卡；[API 契约](07-api.md#8-api-契约)/[前端规格](08-frontend.md#9-前端规格) 作为接口与 UI 契约源；[并发与时序规范](09-concurrency.md#10-并发与时序规范实现红线) 作为 Code Review 红线；[测试规格](11-testing.md#12-测试规格) 作为测试清单。实现前先建立数据库迁移、任务状态机和 Redis Broker 的契约测试，再并行开发前后端。任何偏离 [核心原则](03-architecture.md#42-核心原则架构约束实现不得违背)、[并发与时序规范](09-concurrency.md#10-并发与时序规范实现红线) 的实现都必须更新本文与 ADR，不能只通过代码注释隐式改变需求。
