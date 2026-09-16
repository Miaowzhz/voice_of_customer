# Hermes Agent 交互设计研究笔记

> 研究日期：2026-09-16  
> 研究范围：Hermes Agent 官方仓库与官方文档，重点关注消息入口、会话、任务执行、工具调用、流式反馈和扩展机制。  
> 资料来源均为 Nous Research 的官方仓库或官方文档。

## 一、项目定位

Hermes Agent 将同一套 Agent 核心复用到 CLI、消息网关、TUI、桌面应用等入口。消息网关负责连接多个平台、维护会话、执行定时任务和投递结果，平台适配器只负责接收和发送消息，再把事件交给统一的 Agent 核心处理。[官方仓库 README](https://github.com/NousResearch/hermes-agent#hermes-agent) [官方消息网关文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging)

官方架构文档将入口、`AIAgent`、会话存储和工具后端分层：入口层包括 Gateway、API Server、CLI 等；`AIAgent` 负责提示词、模型选择和工具分发；会话元数据与消息历史使用 SQLite；工具后端可以是本地终端、浏览器、网络工具或 MCP。[官方架构文档](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)

## 二、与飞书交互的关键做法

### 1. 统一网关接入

Hermes 使用一个后台 Gateway 进程连接多个消息平台，每个平台适配器将消息转换成统一事件，再按会话路由给 Agent。飞书适配器支持私聊、群聊、文件、图片、音频、线程、反应和交互卡片。[官方消息网关文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging) [官方飞书适配器文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/feishu)

飞书支持 WebSocket 长连接和 Webhook 两种模式。官方推荐 WebSocket，适合本地电脑、内网服务器或不具备公网地址的部署；SDK 负责连接保活和重连。[官方飞书适配器文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/feishu#step-2-choose-a-connection-mode)

### 2. 私聊、群聊和 @ 机制

Hermes 默认私聊直接响应，群聊只有在 @ 机器人时响应。群聊可以按用户隔离会话，也可以配置成整个群共用一个会话；默认是按用户隔离。[官方飞书适配器文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/feishu#how-hermes-behaves) [官方会话生命周期文档](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-session-lifecycle#5-multi-user-isolation-strategy)

生产环境可以配置飞书 Open ID 白名单。Hermes 还提供私聊配对机制，让未知用户先获得一次性验证码，管理员批准后才可使用。[官方消息网关文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging#security)

### 3. 会话键与持久化

Hermes 为每条消息建立 `SessionSource`，包含平台、聊天、用户、线程和消息 ID。会话键按 `agent:main:{platform}:{chat_type}:{chat_id}:{thread_id}:{participant_id}` 这类规则生成，从而保证不同客户、群组和线程之间不会串上下文。[官方会话生命周期文档](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-session-lifecycle#1-sessionsource-message-origin-descriptor) [官方会话生命周期文档](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-session-lifecycle#4-sessionkey-generation-rules)

会话元数据、完整消息历史和模型使用信息保存到 SQLite；数据库启用 WAL，适合多平台并发读写。网关还把待投递回复写入 delivery ledger，进程重启后可以重新投递，避免分析已经完成但用户收不到结果。[官方会话存储文档](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage) [官方消息网关文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging#delivery-reliability)

### 4. 长任务与忙碌中的新消息

Hermes 收到消息后可以立即确认，再通过 `/bg` 启动独立的后台会话；主会话继续可用，后台任务完成后把结果发送回原聊天。[官方消息网关文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging#background-sessions)

Agent 正在工作时，用户的新消息支持三种策略：中断当前轮次、排队等待下一轮，或 steer 到当前运行中。网关可以发送忙碌确认，告诉用户消息已经收到。[官方消息网关文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging#queue-vs-interrupt-vs-steer-busy-input-mode)

### 5. 流式状态与工具进度

Agent 核心暴露 `tool_progress_callback`、`step_callback`、`stream_delta_callback` 等回调。网关可以把工具执行状态、思考状态和文本增量发送给平台；支持消息编辑的平台可以持续编辑同一个消息气泡，减少刷屏。[官方 Agent Loop 文档](https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop#callback-surfaces) [官方消息网关文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging#tool-progress-notifications)

Hermes 还支持状态短语和“静默”标记。当结果没有必要通知群聊时，Agent 可以返回 `[SILENT]`，网关保存这次会话但不向聊天发送消息。[官方消息网关文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging#intentional-silence-tokens)

## 三、工具调用与扩展机制

### 工具和工具集

Hermes 将工具注册到统一的工具注册表，再按平台或 Agent 配置启用工具集。工具覆盖文件、终端、浏览器、视觉、记忆、定时任务、澄清问题和子 Agent 委派等能力。[官方工具文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools)

模型返回多个工具调用时，Hermes 可以并发执行，再按原调用顺序写回工具结果；交互式工具（例如澄清问题）需要串行执行。每次工具调用前后都可以触发插件钩子，危险操作还要经过用户确认。[官方 Agent Loop 文档](https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop#tool-execution)

### Skills

Skills 是按需加载的知识和流程文档，遵循渐进披露原则，避免每次请求都把完整说明放进提示词。用户可以在 CLI 或消息平台使用 `/技能名` 调用；Agent 也可以在复杂任务完成后生成和改进技能。[官方 Skills 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)

### MCP

MCP 用于连接 Hermes 外部的工具服务器。官方配置同时支持本地 stdio 和远程 HTTP MCP，并在启动时自动发现工具，还可以按服务器过滤暴露的工具。[官方 MCP 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)

### 核心保持窄接口

Hermes 的开发指南强调“核心保持窄接口，能力放在边缘”：新增能力优先通过平台适配器、Skill、服务门控工具或插件接入，避免不断扩大每次模型请求都携带的核心工具集合。[官方 AGENTS.md](https://github.com/NousResearch/hermes-agent/blob/main/AGENTS.md)

## 四、对本项目飞书交互的重新设计建议

下面是基于上述官方事实形成的项目设计推断，属于本项目的实现建议，不是 Hermes 的现成代码。

### 推荐链路

```text
飞书 WebSocket
    ↓
飞书适配器：校验权限、去重、解析文本/文件/多维表格链接
    ↓
统一消息事件：chat_id、user_id、thread_id、message_id、附件
    ↓
会话路由：每个客户聊天建立独立会话
    ↓
意图识别：分析反馈 / 查询任务 / 查看报告 / 取消任务
    ↓
任务管理器：创建 run_id，立即发送确认消息
    ↓
LangGraph 后台执行：清洗 → 好中差评价 → 分析 → 图表 → 持久化
    ↓
进度更新：编辑同一条状态消息，避免连续刷屏
    ↓
结果投递：摘要卡片 + 等级饼图 + 好评词云 + 报告链接
    ↓
投递记录：保存发送状态，异常重连后补发
```

### 建议新增的用户交互

| 交互 | 作用 | 设计依据 |
| --- | --- | --- |
| 发送文件或多维表格链接 | 创建一次 VOC 分析任务 | Hermes 的统一消息入口和媒体接收 |
| `分析中` 确认消息 | 让用户知道任务已接收 | Hermes 的后台任务与忙碌确认 |
| `查看进度` | 返回当前节点、已处理数量和预计状态 | Hermes 的工具进度回调 |
| `查看报告 <run_id>` | 返回 Markdown/JSON 报告和图片 | Hermes 的会话持久化与结果投递 |
| `取消任务 <run_id>` | 中断长时间分析 | Hermes 的 `/stop` 和可中断模型调用 |
| `重新开始` | 清空当前会话上下文 | Hermes 的 `/new` / `/reset` |
| `重试任务 <run_id>` | 复用原始数据重新执行失败节点 | Hermes 的 `/retry` 与后台任务思路 |

### 推荐的数据边界

1. 飞书适配器只处理平台协议、权限、去重、附件下载和消息发送。
2. 任务管理器只处理 run_id、状态、取消、重试和投递记录。
3. LangGraph 只处理 VOC 分析状态和节点编排，不直接依赖飞书 SDK。
4. Feishu Bitable、Excel、CSV、JSON 读取封装成独立工具；后续可以实现为本地工具或 MCP 服务。
5. VOC 分析规则、输出格式和复核标准放入独立 Skill，避免把业务规则硬编码到网关核心。

### 针对本项目的取舍

- **第一阶段**：保留现有 LangGraph，新增“会话路由 + 任务状态命令 + 单消息进度更新 + 可靠投递记录”。
- **第二阶段**：把飞书多维表格读取、报告查询和任务管理封装为工具，必要时通过 MCP 暴露给 Agent。
- **第三阶段**：新增 `voc-analysis` Skill，让分析口径、字段要求、异常处理和报告格式可配置、可版本化。
- **安全边界**：飞书只允许白名单用户或配对用户访问；分析任务使用独立工作目录；Agent 不直接获得任意终端权限。

## 五、官方参考链接

- [Hermes Agent 官方仓库](https://github.com/NousResearch/hermes-agent)
- [Hermes Agent 官方文档](https://hermes-agent.nousresearch.com/docs/)
- [消息网关](https://hermes-agent.nousresearch.com/docs/user-guide/messaging)
- [飞书 / Lark 适配器](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/feishu)
- [会话生命周期](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-session-lifecycle)
- [会话存储](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage)
- [Agent Loop](https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop)
- [工具和工具集](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools)
- [Skills 系统](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- [MCP 集成](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [系统架构](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)
