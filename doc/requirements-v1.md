# AI Coding 工作流框架 —— 需求分析文档 v1

## 1. 背景

当前 AI Coding（LLM Agent 辅助编程）存在两个已知且未解决的架构性问题：

**问题 A — 上下文失控**
LLM 的上下文窗口是线性的，对话越长，早期信息越容易被稀释（lost in the middle）。LLM 没有"定向删除某段上下文"或"上下文出栈"的能力。依赖 LLM 自身做上下文压缩不可靠，压缩后的幻觉率显著上升。

**问题 B — 工作流不确定**
AI Agent 的行为由 prompt/skill 等软性约束引导，无法保证 100% 执行。即使将规则写成文档，Agent 仍可能忽略或选择性执行。对于多步骤、多角色、多检查点的复杂工作流，必须将流程逻辑写死在代码中，而非留给 Agent 自行判断。

## 2. 项目目标

开发一套 AI Coding 工作流框架，从架构层面解决上述两个问题，使团队能够搭建确定性高、可观察、可干预的 AI 开发工作流。

## 3. 第一版范围

### In Scope
- 工具框架的设计与实现（Agent 管理、对话管理、状态持久化、日志、配置文件管理）
- 一条 Web 全栈开发工作流的实现（作为框架的验证案例，并可用于实际生产）
- 将 `orchestrated-fullstack-delivery` skill 中的工作方法（阶段划分、验证规则、人机检查点）硬性编码到工作流中

### Out of Scope
- 其他类型的工作流（数据分析、MLOps 等）—— 后续版本
- 多人协作的权限管理 —— 后续版本
- Web UI 界面 —— 仅 CLI 使用
- 进程崩溃后自动恢复工作流进度 —— 后续版本

## 4. 角色定义

| 角色 | 描述 |
|:----|:------|
| **用户** | 使用工作流的团队开发者 |
| **Master Agent** | 工作流的编排者，接收用户指令，分解任务，协调子 Agent |
| **Worker Agent** | 执行具体任务的 Agent（Dev、QA、Reviewer 等），通过 Hermes Gateway API Server 调用 |

## 5. 功能需求

### FR-1 Agent 管理

**FR-1.1 注册 Agent**
- 支持注册一个新的 Agent（指定 name、profile、port、api_key）
- 注册时自动检测端口是否已运行 Hermes Gateway
- 检测通过后进一步验证 profile 是否匹配（调用 API 读取 model 字段）

**FR-1.2 创建 Hermes Profile**
- 注册 Agent 时，如果对应的 Hermes Profile 不存在，工具框架自动创建
- 创建方式：`hermes profile create --clone-from <source_profile>`
- 复制内容：config.yaml
- 按需复制：.env（API_SERVER_ENABLED、API_SERVER_KEY、API_SERVER_PORT 由框架写入）
- 不自动复制 soul.md、user.md、memory.md，由用户指定

**FR-1.3 启动/停止 Gateway**
- 支持启动 Agent 的 Gateway 进程（后台，新控制台窗口）
- 启动时通过环境变量注入 API_SERVER_PORT、API_SERVER_ENABLED、API_SERVER_KEY
- 支持停止 Gateway 进程，释放所有对话上下文
- 支持查询 Gateway 运行状态

**FR-1.4 删除 Agent**
- 物理删除 Agent：先停止 Gateway，再从注册信息中移除

### FR-2 对话管理

**FR-2.1 多对话隔离**
- 一个 Agent 支持多个对话，不同 conversation 的上下文互不干扰
- 基于 Hermes Gateway API 的 conversation 参数实现

**FR-2.2 调用 Agent 对话**
- 支持向指定 Agent 的指定对话发送消息
- 记录每次调用的耗时、输入 token 数、输出 token 数

**FR-2.3 关闭对话**
- 在 Agent Pool 层面停止跟踪指定对话，不再向其发消息
- 服务端（Hermes Gateway）的对话数据不做清理，按默认策略保留

### FR-3 状态持久化

**FR-3.1 持久化内容**
- Agent 注册信息（name、profile、port、api_key、status）
- 各 Agent 当前的对话列表

**FR-3.2 文件格式**
- 使用 JSON 文件存储，路径固定，可手工查看和编辑

**FR-3.3 生命周期**
- 进程重启后能从文件恢复上一次的状态，无需重新注册

### FR-4 日志

**FR-4.1 Agent 调用日志**
每次 Agent 调用需记录：
- 时间戳
- Agent 名称
- 对话名称
- 输入文本长度（字符数）
- 输出文本长度（字符数）
- 耗时（秒）

**FR-4.2 Agent 生命周期日志**
每次 Agent 生命周期变更需记录：
- 创建、启动、停止、删除
- 对应的时间戳和操作结果

**FR-4.3 日志格式**
- JSONL（每行一条 JSON 记录），便于程序化分析

### FR-5 人工检查点

**FR-5.1 定义检查点**
- 用户可在工作流定义中标记某些节点为人工检查点

**FR-5.2 检查点行为**
- 到达检查点时，Master Agent 暂停执行
- 向用户报告当前进度和前置结果
- 等待用户确认/修改后继续执行

### FR-6 异常处理

| 异常场景 | 处理策略 |
|:---------|:---------|
| 调用 Agent 超时 | 自动重试，最多 3 次 |
| 3 次重试均超时 | 向用户报告错误，暂停工作流 |
| Gateway 挂了 | 尝试自动重启，失败则报给用户 |
| LLM 返回格式不符合预期 | 记录原始输出后重试，超过阈值则报错 |

### FR-7 Web 全栈开发工作流

**FR-7.1 工作流定义**
- 工作流由代码硬性定义（LangGraph 或等价方案），而非 skill 软约束
- 工作流需包含以下阶段：
  1. 需求澄清 —— 理解用户需求，确认范围
  2. 出方案 —— 生成实现方案
  3. 方案评审（可循环） —— 评审方案，不通过则返回出方案阶段
  4. 实现 —— 按方案编写代码
  5. 测试 —— 运行测试，验证功能
  6. 交付 —— 汇总结果

**FR-7.2 硬性编码的规则**
- 参考 orchestrated-fullstack-delivery skill，将以下规则写入工作流代码：
  - 评审和实现分离
  - 约束在每个任务边界重复注入
  - 每步完成后验证结果

**FR-7.3 人工检查点**
- 工作流的关键阶段切换处插入人工检查点（如方案通过后、交付前）
- 到达检查点时，Master Agent 暂停并报告进度

**FR-7.4 可观测性**
- 用户可随时查看当前进度、执行到哪一步、各步的结果

### FR-8 性能指标记录

每次 Agent 调用需记录并输出到日志：
- 响应时间（秒）
- 输入 tokens 数
- 输出 tokens 数
- 总 tokens 数

## 6. 非功能需求

| 编号 | 需求 | 说明 |
|:----|:-----|:------|
| NFR-1 | 可观测性 | 用户无需读源码即可了解工作流当前状态 |
| NFR-2 | 模块化 | 工具框架与业务工作流解耦，工具框架可直接复用 |
| NFR-3 | 低侵入 | Worker Agent 不需要额外代码，标准 Hermes Gateway 即可 |

## 7. 约束

- Agent 调用基于 Hermes Gateway API Server（`POST /v1/responses`）
- 工作流编排使用 LangGraph
- 运行环境为 Windows
- Agent 通过 conversation 参数实现多对话隔离
- 本版本不处理进程崩溃恢复，假设工作流在单次执行内完成
