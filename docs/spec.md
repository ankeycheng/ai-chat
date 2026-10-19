# AI 聊天应用开发规格说明书（Spec-Driven Development）

| 项 | 内容 |
|---|---|
| 文档版本 | v0.1（初始版本，规格驱动开发基线） |
| 状态 | 已实现并完成首轮验收（证据见 [验收清单](spec/11-testing.md#13-验收清单definition-of-done) 与 `docs/`）；遗留项见 [已知限制与后续演进](spec/12-limitations-and-adrs.md#14-已知限制与后续演进) |
| 依据 | 产品需求基线；DeepSeek 官方 API 文档 2026-10-17 版（见 [官方 API 参考](spec/appendix-a-deepseek-api.md)、[Search API 调研与验证记录](spec/appendix-c-search-verify.md)） |
| 技术基线 | React 19.2 + Vite + TypeScript、Python 3.13 + FastAPI（uv 管理）、SQLAlchemy 2 async + Alembic、Redis Streams、PostgreSQL（本地可 SQLite）、DeepSeek Chat Completions + 独立 Search/Fetch API、Docker Compose + Nginx |
| 术语约定 | MUST/SHOULD/MAY 按 RFC 2119；P0/P1/P2 为交付优先级 |
| 读者 | 后端、前端、测试、运维 |

**版本历史**

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.1 | 2026-10-19 | 初始版本（规格驱动开发基线） |

> **范围说明**：本版不承诺任务自动故障转移或自动重跑；实例崩溃后应恢复已落在 Redis 的事件，并把任务明确标记为中断。核心能力包括：游标与多标签页恢复、生成互斥与幂等、终态事件、Redis 裁剪、任务崩溃检测、DeepSeek 搜索 API 契约；以及历史上下文组装、多窗口会话列表实时同步（全局 SSE）、会话搜索、会话重命名与自动标题、ChatGPT/Grok 风格 UI 与响应式布局、`DB_BACKEND`/`BROKER_BACKEND` 配置开关与启动校验、uv 管理的分层后端、Docker 一键部署。

章节已按功能拆到 [`docs/spec/`](spec/)；本页为总入口与索引。需求语义以各子文件为准。

---

## 目录（按功能）

### 目标与范围

- [背景与目标](spec/01-goals-and-scope.md#1-背景与目标) · [成功标准](spec/01-goals-and-scope.md#11-成功标准验收总纲)
- [范围（In / Out of Scope）](spec/01-goals-and-scope.md#2-范围)
- [术语](spec/02-glossary.md)

### 架构

- [系统架构与组件](spec/03-architecture.md#41-组件)
- [核心原则（A1–A10）](spec/03-architecture.md#42-核心原则架构约束实现不得违背)
- [后端分层](spec/03-architecture.md#43-后端分层must)

### 会话 · 生成与 SSE · 搜索

- [会话管理](spec/04-features.md#51-会话管理)
- [消息发送与生成](spec/04-features.md#52-消息发送与生成)
- [思考开关](spec/04-features.md#53-思考开关)
- [联网搜索](spec/04-features.md#54-联网搜索) · [DeepSeek Search API 契约](spec/04-features.md#541-deepseek-search-api-契约经官方文档核对与真实调用验证)
- [SSE 订阅与断线续传](spec/04-features.md#55-sse-订阅与断线续传核心)
- [多实例广播与 Stream 生命周期](spec/04-features.md#56-多实例广播与-stream-生命周期)
- [中断与故障](spec/04-features.md#57-中断与故障)
- [多窗口实时同步](spec/04-features.md#58-多窗口实时同步p0)
- [历史上下文组装规则](spec/04-features.md#59-历史上下文组装规则p0)
- [自动标题](spec/04-features.md#510-自动标题p1)
- [会话搜索](spec/04-features.md#511-会话搜索p0)

### 前端与多窗口

- [前端结构 / 状态 / 加载恢复](spec/08-frontend.md)
- [消息合并算法](spec/08-frontend.md#94-消息合并算法must)
- [视觉风格 · 响应式 · 多窗口同步](spec/08-frontend.md#96-视觉与交互风格chatgptgrok-风格)

### 数据与 API

- [非功能需求](spec/05-nfr.md)
- [数据模型与 Redis 键](spec/06-data-model.md)
- [API 契约与错误码](spec/07-api.md)
- [SSE 事件协议](spec/07-api.md#83-sse-事件协议) · [全局 SSE 协议](spec/07-api.md#84-全局-sse-协议)

### 并发 · 部署

- [并发与时序红线（C1–C17）](spec/09-concurrency.md)
- [部署与配置](spec/10-deploy.md)
- [启动校验 · Docker · 迁移 · uv](spec/10-deploy.md#112-启动校验)

### 测试 · ADR · 附录

- [测试规格](spec/11-testing.md#12-测试规格) · [验收清单 DoD](spec/11-testing.md#13-验收清单definition-of-done)
- [已知限制与后续演进](spec/12-limitations-and-adrs.md#14-已知限制与后续演进)
- [决策记录（ADR 摘要）](spec/12-limitations-and-adrs.md#15-决策记录adr-摘要)
- [附录 A · 官方 API 参考](spec/appendix-a-deepseek-api.md)
- [附录 B · 实现决策与假设](spec/appendix-b-decisions.md)
- [附录 C · Search API 调研与验证](spec/appendix-c-search-verify.md)
