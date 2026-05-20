# AgentRuntime API 参考文档

> 对应文件：`src/agent_runtime.py`

---

## 工具函数（模块级）

### `_iso_now() -> str`
返回当前时间的 ISO 8601 格式字符串。
- **输出：** `"2026-05-19T14:30:00"`
- **边界情况：** 无。纯时间格式化，不依赖任何外部状态。

### `_read_json(path: str) -> dict`
读取 JSON 文件。
- **参数：** `path` — 文件路径
- **输出：** 解析后的 dict
- **边界情况：**
  - 文件不存在 → 返回 `{}`
  - 文件内容不是合法 JSON → 抛出 `json.JSONDecodeError`
  - 文件编码非 UTF-8 → 抛出 `UnicodeDecodeError`

### `_write_json(path: str, data: dict)`
将 dict 写入 JSON 文件。
- **参数：** `path` — 文件路径，`data` — 要写入的数据
- **输出：** 无
- **行为：** 自动创建父目录（`os.makedirs`）。使用 `ensure_ascii=False` 保留 Unicode。
- **边界情况：**
  - 父目录不存在 → 自动创建
  - 路径不可写 → 抛出 `PermissionError`
  - `data` 含不可序列化类型（如 datetime）→ 抛出 `TypeError`

### `_append_jsonl(path: str, record: dict)`
向 JSONL 文件追加一行。
- **参数：** `path` — 文件路径，`record` — 要追加的记录
- **输出：** 无
- **行为：** 自动创建父目录。每行一条 JSON，末尾加 `\n`。
- **边界情况：**
  - 文件不存在 → 自动创建
  - 同上 `_write_json` 的异常情况

---

## 结果类型（Dataclass）

### `CreateAgentResult`
| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `success` | `bool` | 是否成功 |
| `message` | `str` | 结果描述 |
| `status` | `str` | `"running"` 或 `"stopped"` |

### `RunGatewayResult`
| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `success` | `bool` | 是否成功 |
| `message` | `str` | 结果描述 |
| `pid` | `Optional[int]` | 进程 ID，失败时为 `None` |

### `StopGatewayResult`
| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `success` | `bool` | 是否成功 |
| `message` | `str` | 结果描述 |

### `DropAgentResult`
| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `success` | `bool` | 是否成功 |
| `message` | `str` | 结果描述 |

### `CallResult`
| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `success` | `bool` | 是否成功 |
| `text` | `str` | 提取的纯文本（所有 output_text 拼接） |
| `input_tokens` | `int` | 输入 token 数（整个 conversation 累计） |
| `output_tokens` | `int` | 输出 token 数（累计） |
| `latency_ms` | `int` | 总耗时（含重试） |
| `error` | `Optional[str]` | 失败原因 |
| `raw_data` | `Optional[dict]` | Hermes 返回的完整 JSON |

- **`raw_data` 格式参考：**
  ```json
  {
    "id": "resp_xxx",
    "status": "completed",
    "output": [
      {"type": "function_call", "name": "execute_code", ...},
      {"type": "function_call_output", ...},
      {"type": "message", "content": [{"type": "output_text", "text": "..."}]}
    ],
    "usage": {"input_tokens": 100, "output_tokens": 50}
  }
  ```

### `CheckpointResult`
| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `action` | `str` | `"continue"` / `"modify"` / `"reject"` |
| `message` | `str` | 用户输入的文字 |

### `ProgressReport`
> ⚠️ 已废弃。`ContextManager` 改用三段式结构，不再使用此类型。

---

## 1. AgentManager

负责 Agent 注册、Gateway 生命周期管理。

### `__init__(pool_dir: str, hermes_home: str)`
- **参数：**
  - `pool_dir` — `.agent_runtime` 目录路径
  - `hermes_home` — Hermes 安装根目录
- **行为：** 从 `registry.json` 加载已注册的 agent 列表。
- **边界情况：**
  - `registry.json` 不存在 → 视为空注册表
  - `registry.json` 内容损坏 → 抛出 `json.JSONDecodeError`

### `create_agent(name, profile, port, api_key="kaguya") -> CreateAgentResult`
注册一个新 agent。
- **参数：** `name` — agent 名，`profile` — Hermes profile 名，`port` — gateway 端口，`api_key` — API 密钥
- **行为：**
  1. 检查 `name` 是否已存在
  2. 如果 profile 不存在，自动创建（clone from `cg`）
  3. 写 `.env` 文件到 profile 目录（注入 `API_SERVER_PORT` / `API_SERVER_ENABLED` / `API_SERVER_KEY`）
  4. 检测端口上是否有已运行的 Gateway
  5. 写入 `registry.json`
- **边界情况：**
  - `name` 已存在 → 返回 `success=False, message="agent xxx 已存在"`
  - profile 不存在且 `hermes` CLI 不可用 → `subprocess` 抛出 `FileNotFoundError`
  - 端口上已有其他服务 → 检测失败，status 为 `"stopped"`
  - port 被占用且是 Hermes Gateway → 自动识别为 `"running"` 并校验 profile 是否匹配

### `run_gateway(agent) -> RunGatewayResult`
启动指定 agent 的 Gateway 进程。
- **参数：** `agent` — agent 名
- **行为：**
  1. 从 registry 读取 port/profile/api_key
  2. 设置环境变量 `API_SERVER_PORT` / `API_SERVER_ENABLED` / `API_SERVER_KEY`
  3. 用 `subprocess.Popen` 启动 `hermes --profile xxx gateway run`
  4. 轮询 30 秒，每秒检查 `/health` 端点
  5. 就绪后更新 registry 的 status 和 pid
- **边界情况：**
  - agent 不存在 → `success=False, "agent xxx 不存在"`
  - 已在运行 → `success=True, "已在运行", pid=xxx`
  - 30 秒内未就绪 → `success=False, "启动超时"`
  - Windows 平台用 `CREATE_NEW_CONSOLE`，会弹出新控制台窗口

### `stop_gateway(agent) -> StopGatewayResult`
停止 Gateway 进程。
- **参数：** `agent` — agent 名
- **行为：** 用 `taskkill /F /PID xxx` 强制终止进程，清空 conversations 列表。
- **边界情况：**
  - agent 不存在 → `success=False`
  - 未运行 → `success=True, "未运行"`
  - 进程已不存在（被外部杀死）→ `except` 静默忽略，registry 仍更新为 stopped

### `drop_agent(agent) -> DropAgentResult`
物理删除 agent。
- **行为：** 先 `stop_gateway`，再从 registry 中移除整条记录。
- **边界情况：** 同 `stop_gateway`。如果 agent 不存在，先尝试 stop（返回 false），再 pop 会静默跳过。

### `health(agent) -> bool`
检查 Gateway 是否存活。
- **行为：** GET `/health`，超时 3 秒。
- **边界情况：**
  - agent 不存在 → `False`
  - 连接失败/超时 → `False`（所有异常被 except 捕获）

### `list_agents() -> list[dict]`
返回所有 agent 列表。
- **输出：** `[{"name", "profile", "port", "status", "conversations"}, ...]`
- **边界情况：** registry 为空 → `[]`

### `get_config(agent) -> Optional[dict]`
返回 agent 的配置 dict。
- **边界情况：** agent 不存在 → `None`

---

## 2. ConversationManager

负责对话调用、初始化、关闭。

### `__init__(agent_mgr, logger, config, pool_dir)`
- **参数：** `agent_mgr` — AgentManager 实例，`logger` — Logger 实例，`config` — Config 实例，`pool_dir` — 数据目录

### `call(agent, conversation, input_text, timeout=None) -> CallResult`
向指定 agent 的指定对话发送消息。
- **行为：**
  1. 从 registry 读取 port/api_key
  2. POST `/v1/responses` 到 Gateway
  3. 超时自动重试（最多 `max_retry + 1` 次）
  4. 成功后提取 output_text、记录日志、追踪 conversation
  5. 失败后记录错误日志
- **边界情况：**
  - agent 不存在 → `CallResult(success=False, error="agent xxx 不存在")`
  - gateway 未运行 → `CallResult(success=False, error="xxx gateway 未运行")`
  - 每次超时 → 记 `call_retried` 事件，sleep 1s 后重试
  - 所有重试均失败 → `CallResult(success=False, error="重试N次失败: ...")`
  - HTTP 返回非 200 → 记 `last_error` 继续重试
  - 连接异常（ConnectionError等）→ 被 except 捕获，继续重试
- **重试策略：** `max_retry` 次重试（默认 3），加上首次尝试共 4 次。每次重试间隔 1 秒。

### `init_conversation(agent, conversation, initial_prompt) -> CallResult`
初始化一个对话。
- **行为：** 先 `close_conversation`（从 registry 移除追踪），再 `call` 发送第一条消息。
- **注意：** 不清除 Hermes 服务端的旧对话数据。如果要彻底隔离，调用方应在 conversation 名中加入唯一标识（如时间戳）。

### `close_conversation(agent, conversation)`
停止追踪指定对话。
- **行为：** 从 registry 的 `conversations` 列表中移除该 conversation 名。
- **边界情况：**
  - agent 不存在 → 静默跳过
  - conversation 不在列表中 → 静默跳过
  - **不清除 Hermes 服务端数据**——服务端的对话历史仍然保留

### `_track_conversation(agent, conversation)`（内部方法）
将 conversation 名加入 registry 的追踪列表。
- **行为：** 读 registry → 追加（去重）→ 写回。
- **边界情况：** agent 不存在 → 静默跳过；重复添加 → 自动去重。

---

## 3. Logger

负责调用日志和事件日志，均写入 JSONL 文件。

### `__init__(pool_dir: str)`
- **行为：** `calls.jsonl` 和 `events.jsonl` 的路径由 `pool_dir` 决定。
- **边界情况：** 文件不会在 `__init__` 时创建，在第一次写入时自动创建。

### `log_call(agent, conversation, input_text, output_text, input_tokens, output_tokens, latency_ms, success, error=None)`
记录一次 agent 调用。
- **写入字段：** timestamp, agent, conversation, input_text, input_length, output_text, output_length, latency_ms, input_tokens, output_tokens, total_tokens, success, error
- **边界情况：**
  - input_text/output_text 为空 → 正常记录（长度 0）
  - 含特殊字符（换行、制表符、引号）→ JSON 序列化自动转义
  - 大文本 → 直接写入，不做截断

### `log_event(event_type, agent=None, detail=None)`
记录一次事件。
- **允许的 `event_type`：** `agent_created`, `gateway_started`, `gateway_stopped`, `agent_deleted`, `phase_started`, `phase_completed`, `call_retried`, `error`, `workflow_started`, `workflow_ended` 等
- **边界情况：** agent 和 detail 可为 None

### `get_calls(agent=None, conversation=None, limit=50) -> list[dict]`
查询调用记录。
- **参数：** `agent` — 按 agent 过滤，`conversation` — 按对话过滤，`limit` — 返回最近 N 条
- **边界情况：**
  - 文件不存在 → `[]`
  - 文件为空 → `[]`
  - 过滤条件全不传 → 返回全部
  - 行内容为空行时跳过
  - limit 超出实际条数 → 返回全部

### `get_events(agent=None, event_type=None, limit=50) -> list[dict]`
查询事件记录。同上。

---

## 4. ContextManager

三段式上下文管理：background / phase（树状） / contexts。

### `__init__(pool_dir: str)`
- **行为：** 从 `context.json` 加载数据。文件不存在时视为空数据。

### `set_bg(key, value)`
写入 background 段。
- **示例：** `set_bg("project_name", "博客系统")`
- **存储结构：** `{"background": {"key": "value"}}`

### `get_bg(key) -> str`
读取 background 段。key 不存在返回 `""`。

### `set_phase_node(path, status, title=None)`
创建或更新 phase 树节点。
- **参数：**
  - `path` — 路径列表，如 `["顶层规划", "后端实现", "文章 CRUD"]`
  - `status` — `"todo"` / `"wip"` / `"done"`
  - `title` — 可选，更新节点标题
- **行为：**
  - 沿 path 逐层查找/创建节点
  - 只有 path 最后一个元素会设置 status
  - 根节点的 status 只会在 `len(path)==1` 时设置
- **边界情况：**
  - path 为空列表 → 只创建根节点（title 空字符串）
  - 重复调用同一个 path → 更新 status
  - 路径中有已存在的节点 → 复用，不覆盖 children
  - `title` 不传 → 保留原有标题

### `get_phase_text(indent=0) -> str`
渲染 phase 树为缩进文本。
- **输出格式：** `● 顶层规划  [wip]\n  ○ 子步骤  [todo]`
- **图标映射：** `done`→✓, `wip`→●, `in_progress`→◕, `todo`→○, 其他→?
- **边界情况：**
  - 根节点无 title → 返回 `""`
  - 只有根节点 title 没有子节点 → 显示单行
  - 节点 status 为 `done` 时不展开子节点（但已完成的节点仍显示）

### `set_ctx(key, value)`
保存上下文文本到 contexts 段。
- **存储结构：** `{"contexts": {"key": "value"}}`

### `get_ctx(key) -> str`
读取上下文文本。key 不存在返回 `""`。

### `build_injection(keys) -> str`
将指定 keys 组装成一段文本，用于 Master context 注入。
- **支持的 key 类型：**
  - `"background"` → `== 项目信息 ==\n  key: value`
  - `"phase"` → `== 进度 ==\n{get_phase_text()}`
  - 其他自定义 key → `== {key} ==\n{get_ctx(key)}`
- **边界情况：**
  - key 不存在 → 跳过（不抛异常）
  - 所有 key 为空 → 返回 `""`
  - 多 key 用 `\n\n` 分隔

---

## 5. Config

配置读写（JSON 文件）。

### `__init__(pool_dir: str)`
- **行为：** 从 `config.json` 加载。文件不存在时视为空 dict。

### 默认值
```python
DEFAULTS = {
    "call_timeout": 120,
    "max_retry": 3,
    "max_plan_loop": 5,
    "max_bug_loop": 5,
}
```

### `set(key, value)`
设置配置项。立即写入文件。

### `get(key)`
读取配置项。key 不存在则返回 `DEFAULTS` 中的值。
- **边界情况：** key 既不在文件中也不在 DEFAULTS 中 → 返回 `None`

---

## 6. Checkpoint

人工检查点——暂停工作流，等待用户输入。

### `wait(title, content, prompt="确认无误请按 Enter 继续，或输入修改意见：") -> CheckpointResult`
- **行为：**
  1. 打印分隔线 `=====`
  2. 打印 `【title】`
  3. 打印 content
  4. 打印 prompt，等待用户输入
  5. 解析输入
- **输入解析规则：**
  - 空输入 → `action="continue", message=""`
  - 输入 `"reject"` 或 `"退回"` → `action="reject", message=""`
  - 其他 → `action="modify", message=用户输入`
- **边界情况：**
  - 非交互式终端（无 stdin）→ `input()` 抛出 `EOFError`
  - 输入仅为空白字符 → 视为 continue
  - 大小写不敏感（`REJECT` / `Reject` 都算 reject）

---

## 7. AgentRuntime

顶层编排，聚合全部模块。可通过配置文件传入路径。

### `__init__(config_path=None)`
- **参数：** `config_path` — JSON 配置文件路径。不传则使用默认值。
- **配置文件格式（runtime_config.json）：**
  ```json
  {
    "pool_dir": ".agent_runtime",
    "hermes_home": "C:/Users/温学周/AppData/Local/hermes"
  }
  ```
- **默认值：**
  - `pool_dir` = `".agent_runtime"`（当前工作目录下）
  - `hermes_home` = 自动检测（依次检查 `~/AppData/Local/hermes`、`C:\Users\温学周\AppData\Local\hermes`、`C:\Program Files\hermes`）
- **行为：** 创建 `pool_dir` 目录，实例化 Config、Logger、AgentManager、ContextManager、ConversationManager、Checkpoint

### `_load_config(config_path)`（静态方法）
读取 JSON 配置文件。
- **边界情况：** 文件不存在或格式错误 → 抛出对应异常

### `_detect_hermes_home()`（静态方法）
自动检测 Hermes 安装目录。
- **检测顺序：** 用户 home 目录 → 硬编码路径 → Program Files
- **检测依据：** 目录下存在 `profiles/` 子目录
- **兜底：** 返回 `~/AppData/Local/hermes`

### `__enter__ / __exit__`
上下文管理器。退出时遍历所有 agent，对 status 为 `"running"` 的调用 `stop_gateway`。
- **边界情况：** `__exit__` 始终返回 `False`（不抑制异常）

---

## 调用关系图

```
AgentRuntime
├── Config(pool_dir)              # 读写 config.json
├── Logger(pool_dir)              # 读写 calls.jsonl + events.jsonl
├── AgentManager(pool_dir, hh)    # 读写 registry.json
│   └── 依赖: hermes CLI + profiles 目录
├── ContextManager(pool_dir)      # 读写 context.json
├── ConversationManager(agents, logger, config, pool_dir)
│   └── 读写 registry.json（close_conversation / _track_conversation）
└── Checkpoint()                  # 纯 stdin/stdout，无文件依赖
```

数据文件全部位于 `pool_dir` 目录下：
```
pool_dir/
├── registry.json       # AgentManager + ConversationManager
├── context.json        # ContextManager
├── config.json         # Config
├── calls.jsonl         # Logger
└── events.jsonl        # Logger
```
