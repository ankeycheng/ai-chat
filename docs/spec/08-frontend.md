> 返回 [规格总入口](../spec.md)

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

- `useGlobalSync` 在应用级别保持一条 `/api/events` 连接；`ready` → 重拉列表；`session.*` → `sessionsReducer` 按 version 合并（[全局 SSE 协议](07-api.md#84-全局-sse-协议)）。
- 当前会话标题、状态（生成中）取自同步后的列表数据，因此其他窗口重命名或自动标题会立即反映到顶栏。
- 当前会话被删除（任何窗口）时退出到新对话空状态。
- 打开同一会话的多个窗口各自持有独立会话流与独立游标（不共享，符合 [SSE 订阅与断线续传](04-features.md#55-sse-订阅与断线续传核心)）；本窗口发送的消息在其他窗口经 `start.user_message` 实时出现。
