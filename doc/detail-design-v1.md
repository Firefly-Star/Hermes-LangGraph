# AI Coding 工作流框架 —— 详细设计 v1

## 1. 持久化文件结构

```
<workspace>/
└── .agent_runtime/
    ├── registry.json         # Agent 管理 —— AgentManager
    ├── context.json          # 状态 & 上下文 —— ContextManager
    ├── config.json           # 配置 —— Config
    ├── calls.jsonl           # 调用日志 —— Logger
    └── events.jsonl          # 事件日志 —— Logger
```

各模块只读写自己的文件，不交叉写入。

---

## 2. AgentManager 功能范围

### 管辖的数据
`registry.json`

```json
{
  "agents": {
    "master": {
      "profile": "cg",
      "port": 8642,
      "api_key": "kaguya",
      "status": "running",
      "pid": 12345,
      "conversations": ["clarify-v1", "plan-review"]
    }
  }
}
```

### 各函数边界

| 函数 | 做什么 | 不做什么 |
|:-----|:-------|:---------|
| `create_agent` | 写 registry.json | 不启动 gateway |
| | 检测端口上是否有已运行的 Hermes | 不修改其他模块的文件 |
| | 校验 profile 是否匹配 | |
| `run_gateway` | 读 registry 获取 port/profile | 不修改注册信息（改 status 除外） |
| | 启动子进程，等就绪 | |
| | 更新 status/pid 到 registry | |
| `stop_gateway` | taskkill 进程 | 不清除 conversations 以外的注册信息 |
| | 清除 conversations 列表 | |
| `drop_agent` | 调 stop_gateway | |
| | 从 registry 中移除整条记录 | |
| `health` | 纯查询，无副作用 | |
| `list_agents` | 纯查询，无副作用 | |

---

## 3. ConversationManager 功能范围

### 各函数边界

| 函数 | 做什么 | 不做什么 |
|:-----|:-------|:---------|
| `call` | 读取 registry 获取 port/api_key | 不修改 registry（conversation 列表由调用者负责） |
| | POST 请求到 /v1/responses | 不记录日志（调 Logger 记录） |
| | 超时自动重试（最多 max_retry 次） | |
| | 返回 CallResult | |
| `close_conversation` | 从 registry 的 conversations 列表中移除 | 不发 DELETE 请求到 Hermes |

### 数据流

```
call(agent, conv, input)
    │
    ├──→ AgentManager 的 registry → 拿到 port、api_key
    │
    ├──→ Config → 拿到 call_timeout、max_retry
    │
    ├──→ retry 循环
    │     └── POST http://127.0.0.1:{port}/v1/responses
    │
    ├──→ Logger.log_call(...)    # 记录调用日志
    │
    └──→ 返回 CallResult
```

---

## 4. Logger 功能范围

### 管辖的数据
`calls.jsonl` — 每行一条 JSON
`events.jsonl` — 每行一条 JSON

### 各函数边界

| 函数 | 做什么 | 不做什么 |
|:-----|:-------|:---------|
| `log_call` | 追加一行到 calls.jsonl | 不校验参数内容 |
| `log_event` | 追加一行到 events.jsonl | |
| `get_calls` | 读 calls.jsonl，按条件过滤 | 不修改任何数据 |
| `get_events` | 读 events.jsonl，按条件过滤 | |

### 字段定义

**calls.jsonl — 每行 JSON**

```json
{
  "timestamp": "2026-05-19T14:30:00",
  "agent": "dev",
  "conversation": "plan-v1",
  "input_text": "出方案：博客系统",
  "input_length": 9,
  "output_text": "方案：1. 用户注册...",
  "output_length": 45,
  "latency_ms": 4520,
  "input_tokens": 15232,
  "output_tokens": 57,
  "total_tokens": 15289,
  "success": true,
  "error": null
}
```

**events.jsonl — 每行 JSON**

```json
{
  "timestamp": "2026-05-19T14:30:00",
  "event_type": "agent_created",
  "agent": "dev",
  "detail": "profile=cg, port=8642"
}
```

`event_type` 允许的值：
- `agent_created`
- `gateway_started`
- `gateway_stopped`
- `agent_deleted`
- `phase_started`
- `phase_completed`
- `call_retried`
- `error`
- 文件只追加，不覆盖
- 日志轮转机制暂不实现（后续版本）
- 每条记录自动添加 timestamp（ISO 8601）

---

## 5. ContextManager 功能范围

### 管辖的数据
`context.json`

```json
{
  "background": {
    "project_name": "博客系统",
    "tech_stack": "Flask + SQLite",
    "workspace": "C:/projects/blog",
    "requirements": "用户注册登录、文章CRUD"
  },
  "phase": {
    "title": "顶层规划",
    "status": "wip",
    "children": [
      {
        "title": "后端实现",
        "status": "wip",
        "children": [
          {"title": "用户模型 + 注册 API", "status": "done", "children": []},
          {"title": "文章 CRUD API",      "status": "wip",   "children": []}
        ]
      },
      {
        "title": "前端实现",
        "status": "todo",
        "children": []
      }
    ]
  },
  "contexts": {
    "approved_plan": "1. 注册模块 2. 登录模块 3. 文章 CRUD",
    "backend_summary": "2/3 完成"
  }
}
```

`status` 允许的取值：`"todo"` | `"wip"` | `"done"`

### 各函数边界

| 函数 | 做什么 | 不做什么 |
|:-----|:-------|:---------|
| `set_bg` / `get_bg` | 读写 background 段（键值对） | 不关联其他模块 |
| `set_phase_node` | 沿 path 创建/更新树节点，设 status | 不在 get_phase_text 中展开已完成的子节点 |
| `get_phase_text` | 渲染 phase 树为缩进文本 | 不修改任何数据 |
| `set_ctx` / `get_ctx` | 读写 contexts 段（键值对） | |
| `build_injection` | 按 keys 拼接三段数据为文本 | 不发送请求 |

---

## 6. Config 功能范围

### 管辖的数据
`config.json`

```json
{
  "call_timeout": 120,
  "max_retry": 3,
  "max_plan_loop": 5,
  "max_bug_loop": 5,
  "git_user_name": "Hermes Agent",
  "git_user_email": "agent@hermes.local"
}
```

### 各函数边界

| 函数 | 做什么 | 不做什么 |
|:-----|:-------|:---------|
| `set` | 写 config.json | 不校验值的类型和范围 |
| `get` | 读 config.json，不存在返回 DEFAULTS | |

---

---

## 7. Checkpoint 功能范围

### 各函数边界

| 函数 | 做什么 | 不做什么 |
|:-----|:-------|:---------|
| `wait` | 打印 title + content | 不修改任何持久化数据 |
| | 打印 prompt | |
| | 读取 stdin — 支持单行（直接 Enter）或多行（end_word 结束） | |
| | 解析输入 → 返回结构化结果 | |

### 输入解析规则

```
用户输入空行（直接按 Enter）  →  action="continue", message=""
用户输入修改意见             →  action="modify",   message="用户输入的文字"
用户输入 "reject" / "退回"   →  action="reject",   message="" 或用户的附言
多行输入（传 end_word 时）    →  累积到 end_word 出现后合并，按以上规则解析
```

---

## 8. AgentRuntime 顶层编排

### 各函数边界

| 函数 | 做什么 | 不做什么 |
|:-----|:-------|:---------|
| `__init__` | 加载各模块的持久化文件 | |
| | 实例化各子模块并注入引用 | |
| `__exit__` | 遍历所有 agent，调用 stop_gateway | 不清除持久化文件 |

### 模块间引用关系

```
AgentRuntime
├── self.agents        = AgentManager(registry_path)
├── self.conversations = ConversationManager(registry_path, self.agents, self.logger, self.config)
├── self.logger        = Logger(calls_path, events_path)
├── self.context       = ContextManager(context_path)
├── self.config        = Config(config_path)
└── self.checkpoint    = Checkpoint()
```

`call()` 是唯一一个跨模块调用的枢纽方法：
1. 读 registry → 拿 port
2. 读 config → 拿 timeout
3. POST 请求
4. 写 logger
5. 更新 registry 的 conversations 列表
