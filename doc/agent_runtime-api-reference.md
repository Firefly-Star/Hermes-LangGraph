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

### `_clean_surrogates(obj)`
递归清除 dict/list 中字符串的非法代理对字符（\ud800-\udfff），防止 JSON 序列化时因非法 Unicode 崩溃。
- **参数：** `obj` — 任意可递归对象
- **输出：** 清洗后的对象
- **边界情况：**
  - 字符串含代理对 → 用 `utf-8 errors=replace` 替换为 U+FFFD
  - 非 dict/list/str 类型 → 原样返回

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

负责 Agent 注册信息的 CRUD，不管理 Gateway 进程。Gateway 生命周期由 GatewayManager 负责。

### `__init__(runtime_dir: str)`
- **参数：** `runtime_dir` — `.agent_runtime` 目录路径
- **行为：** 从 `registry.json` 加载已注册的 agent 列表。
- **边界情况：**
  - `registry.json` 不存在 → 视为空注册表
  - `registry.json` 内容损坏 → 抛出 `json.JSONDecodeError`

### `register(name, profile, port, api_key="kaguya")`
注册一个新 agent。
- **行为：** 写入 `registry.json`，status 置为 `"stopped"`。
- **边界情况：** `name` 已存在 → 覆盖。

### `set_port_status(port, status)`
更新 registry 中所有使用该端口的 agent 的 status。
- **边界情况：** port 不存在 → 静默跳过。

### `set_pid(name, pid)`
更新指定 agent 的 pid。
- **边界情况：** agent 不存在 → 静默跳过。

### `drop_agent(agent) -> DropAgentResult`
从 registry 中移除 agent 记录。
- **边界情况：** agent 不存在 → `success=False`。

### `list_agents() -> list[dict]`
返回所有 agent 列表。
- **输出：** `[{"name", "profile", "port", "status", "pid", "conversations"}, ...]`
- **边界情况：** registry 为空 → `[]`

### `get_config(agent) -> Optional[dict]`
返回 agent 的配置 dict。
- **边界情况：** agent 不存在 → `None`

---

## 1b. GatewayManager

负责 Gateway 进程的生命周期和端口检测。不碰 registry。

### `__init__(hermes_home: str)`
- **参数：** `hermes_home` — Hermes 安装根目录

### `health(port) -> bool`
检查端口的 Gateway 是否存活。
- **行为：** GET `/health`，超时 3 秒。

### `detect(port, api_key, expected_profile) -> tuple[str, str]`
检测端口上的 Gateway 是否可用。返回 `("running"|"stopped", detail)`。
- **行为：**
  1. GET `/health` — 不是 Hermes Gateway 则返回 stopped
  2. POST `/v1/responses`（可选）— 验证 profile 是否匹配
  3. API 探测失败时（如旧版 Gateway 不兼容）仍返回 running（health 已确认）
- **边界情况：** 端口无响应 → `("stopped", "端口无响应")`

### `run(profile, port, api_key) -> tuple[bool, str, Optional[int]]`
启动 Gateway 进程。
- **行为：** 设环境变量 → `subprocess.Popen` → 轮询 30 秒等 `/health` 就绪
- **边界情况：** 30 秒内未就绪 → `(False, "启动超时", None)`

### `stop(pid)`
强制停止进程（`taskkill /F /PID`）。
- **边界情况：** pid 为 None → 静默返回

---

## 2. ConversationManager
---

## 2. ConversationManager

负责对话调用、初始化、关闭。

### `__init__(agent_mgr, logger, config, runtime_dir)`
- **参数：** `agent_mgr` — AgentManager 实例，`logger` — Logger 实例，`config` — Config 实例，`runtime_dir` — 数据目录

### `call(agent, conversation, input_text, timeout=None, stream_callback=None, tool_callback=None) -> CallResult`
向指定 agent 的指定对话发送消息。
- **参数：** `stream_callback` — 可选，每收到一个文本块回调 `callback(chunk)`；`tool_callback(name, args)` — 可选，SSE 事件 `response.output_item.added(function_call)` 时实时回调
- **行为：**
  1. 从 registry 读取 port/api_key
  2. POST `/v1/responses` 到 Gateway（传 `stream_callback` 时流式读取）
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

### `__init__(runtime_dir: str)`
- **行为：** `calls.jsonl` 和 `events.jsonl` 的路径由 `runtime_dir` 决定。
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

### `__init__(runtime_dir: str)`
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

配置读写。直接读写 `runtime_config.json`（项目根目录下的配置文件），不再使用独立的 `config.json`。

### `__init__(config_path: str)`
- **参数：** `config_path` — `runtime_config.json` 的路径
- **行为：** 直接从 `runtime_config.json` 加载配置。文件不存在时视为空 dict。
- **注意：** `Config.set()` 会写回 `runtime_config.json`，因此运行时修改的配置项会持久化到同一文件。

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
设置配置项。立即写入 `runtime_config.json`。

### `get(key)`
读取配置项。key 不存在则返回 `DEFAULTS` 中的值。
- **边界情况：** key 既不在文件中也不在 DEFAULTS 中 → 返回 `None`

---

## 6. Checkpoint

人工检查点——暂停工作流，等待用户输入。

### `wait(title, content, prompt="确认无误请按 Enter 继续，或输入修改意见：", end_word=None) -> CheckpointResult`
- **参数：** `end_word` — 可选，传入时进入多行输入模式，持续读取直到 end_word 单独一行出现
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
  - 多行模式：累积到 end_word 后合并，按以上规则解析
- **边界情况：**
  - 非交互式终端（无 stdin）→ `input()` 抛出 `EOFError`，被捕获后视为空输入（continue）
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
    "runtime_dir": "C:/Users/温学周/Desktop/langgraph_test/.agent_runtime",
    "workspace": "C:/Users/温学周/Desktop/langgraph_test",
    "hermes_home": "C:/Users/温学周/AppData/Local/hermes",
    "call_timeout": 120,
    "max_retry": 3,
    "max_plan_loop": 5,
    "max_bug_loop": 5,
    "input_end_word": "EOF"
  }
  ```
- **字段说明：**
  - `runtime_dir` — 运行时数据目录（registry、context、logs、handoffs 等），绝对路径
  - `workspace` — 项目根目录，用于定位测试产出（PRD.md、prototype.html、criteria.md 等），绝对路径
  - `hermes_home` — Hermes 安装根目录
  - 其他配置项（`call_timeout`、`max_retry` 等）由 Config 模块管理，通过 `runtime.config.get()` 读取
- **默认值：**
  - `runtime_dir` 不传时默认 `".agent_runtime"`（当前工作目录下）
  - `workspace` 不传时默认 `os.getcwd()`
  - `hermes_home` 不传时自动检测（依次检查 `~/AppData/Local/hermes`、`C:\Users\温学周\AppData\Local\hermes`、`C:\Program Files\hermes`）
  - 其他配置项走 Config 的 DEFAULTS
- **行为：** 创建 `runtime_dir` 目录，暴露 `self.runtime_dir` 和 `self.workspace`，实例化 Config、Logger、AgentManager、ContextManager、ConversationManager、Checkpoint

### `_load_config(config_path)`（静态方法）
读取 JSON 配置文件。
- **边界情况：** 文件不存在或格式错误 → 抛出对应异常

### `_detect_hermes_home()`（静态方法）
自动检测 Hermes 安装目录。
- **检测顺序：** 用户 home 目录 → 硬编码路径 → Program Files
- **检测依据：** 目录下存在 `profiles/` 子目录
- **兜底：** 返回 `~/AppData/Local/hermes`

### `run_all(configs: dict)`
注册所有 agent，逐端口检测/启动 gateway。
- **参数：** `configs` — `{agent名: {"profile": str, "port": int, "api_key": str}}`
- **行为：**
  1. 遍历 configs 注册全部 agent（registry 写入）
  2. 按端口去重，逐个健康检测
  3. 已有 Hermes Gateway → 检测 profile 匹配
  4. 无服务 → 启动新 gateway 进程
- **边界情况：** 端口被非 Hermes 服务占用 → 抛出 RuntimeError

### `__enter__ / __exit__`
上下文管理器。退出时遍历所有 agent，对 status 为 `"running"` 的调用 `self._gateway.stop(pid)`。
- **边界情况：** `__exit__` 始终返回 `False`（不抑制异常）

---

## 调用关系图

```
AgentRuntime
├── Config(config_path)            # 读写 runtime_config.json
├── Logger(runtime_dir)            # 读写 calls.jsonl + events.jsonl
├── AgentManager(runtime_dir)        # 读写 registry.json（纯 CRUD）
├── GatewayManager(hermes_home)      # gateway 进程生命周期
├── ContextManager(runtime_dir)    # 读写 context.json
├── ConversationManager(agents, logger, config, runtime_dir)
│   └── 读写 registry.json（close_conversation / _track_conversation）
└── Checkpoint()                  # 纯 stdin/stdout，无文件依赖
```

数据文件全部位于 `runtime_dir` 目录下：
```
runtime_dir/
├── checkpoint.json       # Checkpoint（断线重连）
├── registry.json         # AgentManager + ConversationManager
├── context.json          # ContextManager
├── config.json           # Config（从 runtime_config.json 同步）
├── calls.jsonl           # Logger
├── events.jsonl          # Logger
├── artifacts/            # 项目固化文档（project_context.md 等）
├── phases/               # 阶段总结（phase-summary-*.md）
└── handoffs/             # Agent 间通信信件
```
Config 不再在 `runtime_dir` 下独立存储文件，直接读写项目根目录的 `runtime_config.json`。
