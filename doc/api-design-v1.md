# AI Coding 工作流框架 —— 接口设计文档 v1

## 模块总览

```
AgentPool（顶层编排）
├── AgentManager         # Agent 管理
├── ConversationManager  # 对话管理
├── Logger               # 日志
├── ContextManager       # 状态 & 上下文（三段式）
├── Config               # 配置
└── Checkpoint           # 人工检查点
```

---

## 1. AgentManager — Agent 管理

```python
class CreateAgentResult:
    success: bool
    message: str
    status: str           # "running" | "stopped"

class RunGatewayResult:
    success: bool
    message: str
    pid: int | None

class StopGatewayResult:
    success: bool
    message: str

class DropAgentResult:
    success: bool
    message: str

class AgentInfo:
    name: str
    profile: str
    port: int
    status: str           # "running" | "stopped"
    conversations: list[str]


class AgentManager:
    def create_agent(
        self,
        name: str,
        profile: str,
        port: int,
        api_key: str = "kaguya",
    ) -> CreateAgentResult
        """
        注册一个 Agent。
        如果 profile 不存在，自动 hermes profile create --clone-from <source_profile>。
        如果端口上已有 Hermes Gateway 在运行，自动校验 profile 是否匹配。
        """

    def run_gateway(self, agent: str) -> RunGatewayResult
        """
        启动 Agent 的 Gateway 后台进程。
        通过环境变量注入 API_SERVER_PORT / API_SERVER_ENABLED / API_SERVER_KEY。
        等待 Gateway 就绪后返回。
        """

    def stop_gateway(self, agent: str) -> StopGatewayResult
        """停止 Agent 的 Gateway 进程，释放所有对话。"""

    def drop_agent(self, agent: str) -> DropAgentResult
        """物理删除 Agent：停止 Gateway + 移除注册信息。"""

    def health(self, agent: str) -> bool
        """检查 Agent Gateway 是否存活。"""

    def list_agents(self) -> list[AgentInfo]
        """返回所有 Agent 列表。"""
```

---

## 2. ConversationManager — 对话管理

```python
class CallResult:
    success: bool
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    error: str | None       # "超时" | "gateway未运行" | "格式错误" | None


class ConversationManager:
    def call(
        self,
        agent: str,
        conversation: str,
        input_text: str,
        timeout: int = 120,
    ) -> CallResult
        """
        向指定 Agent 的指定对话发送消息。
        内部自动处理重试（最多 retry 次）。
        自动计算耗时、记录日志。
        """

    def close_conversation(self, agent: str, conversation: str) -> None
        """
        停止追踪指定对话。
        服务端 Hermes Gateway 数据不清理，仅框架层面不再引用。
        """

    def init_conversation(
        self,
        agent: str,
        conversation: str,
        initial_prompt: str,
    ) -> None
        """
        初始化一个对话。如果已有同名对话，自动 close 旧的建新的。
        initial_prompt 作为新对话的第一条消息发出。
        """
```

---

## 3. Logger — 日志

```python
class CallLog:
    timestamp: str
    agent: str
    conversation: str
    input_length: int
    output_length: int
    latency_ms: int
    input_tokens: int
    output_tokens: int
    success: bool
    error: str | None

class EventLog:
    timestamp: str
    event_type: str
    agent: str | None
    detail: str | None


class Logger:
    def log_call(
        self,
        agent: str,
        conversation: str,
        input_length: int,
        output_length: int,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        success: bool,
        error: str = None,
    ) -> None
        """记录一次 Agent 调用。"""

    def log_event(
        self,
        event_type: str,
        agent: str = None,
        detail: str = None,
    ) -> None
        """
        记录一次生命周期事件。
        event_type: "agent_created" | "gateway_started" | "gateway_stopped"
                  | "phase_started" | "phase_completed" | "error"
        """

    def get_calls(
        self,
        agent: str = None,
        conversation: str = None,
        limit: int = 50,
    ) -> list[CallLog]
        """查询调用记录。"""

    def get_events(
        self,
        agent: str = None,
        event_type: str = None,
        limit: int = 50,
    ) -> list[EventLog]
        """查询事件记录。"""
```

---

## 4. ContextManager — 状态 & 上下文（三段式）

采用三段式结构：background（静态项目信息）/ phase（树状进度）/ contexts（阶段摘要文本）。

```python
class ContextManager:
    # ── background（项目信息，写一次就不变） ──

    def set_bg(self, key: str, value: str) -> None
        """记录项目信息，如 project_name, tech_stack, workspace, requirements"""

    def get_bg(self, key: str) -> str
        """读取项目信息"""

    # ── phase（树状进度，沿路径展开） ──

    def set_phase_node(self, path: list[str], status: str, title: str = None) -> None
        """
        创建或更新一个 phase 树节点。
        path = ["顶层规划", "后端实现", "文章 CRUD"]
          → 沿路径查找/创建节点，设置 status，可选更新 title
        status: "todo" | "wip" | "done"
        """

    def get_phase_text(self, indent: int = 0) -> str
        """渲染 phase 树为缩进文本（仅展开 wip 子节点）。"""

    # ── contexts（阶段摘要文本） ──

    def set_ctx(self, key: str, value: str) -> None
        """保存关键上下文文本，key 如 "approved_plan", "backend_summary", "qa_report" """

    def get_ctx(self, key: str) -> str
        """读取保存的上下文"""

    # ── 注入组装 ──

    def build_injection(self, keys: list[str]) -> str
        """
        将指定的上下文片段组装成一段文本。
        keys 支持 "background"、"phase" 或自定义 ctx key，
        用于 Master 刷新时 init_conversation 的 initial_prompt。
        """
```

---

## 5. Config — 配置

```python
class Config:
    DEFAULTS = {
        "call_timeout": 120,
        "max_retry": 3,
        "max_plan_loop": 5,
        "max_bug_loop": 5,
        "git_user_name": "Hermes Agent",
        "git_user_email": "agent@hermes.local",
    }

    def set(self, key: str, value) -> None
        """设置配置项。key 如 "call_timeout" """

    def get(self, key: str)
        """读取配置项，不存在则返回 DEFAULTS 中的值。"""
```

---

## 6. Checkpoint — 人工检查点

```python
class CheckpointResult:
    action: str          # "continue" | "modify" | "reject"
    message: str         # 用户输入的附言


class Checkpoint:
    def wait(
        self,
        title: str,
        content: str,
        prompt: str = "确认无误请按 Enter 继续，或输入修改意见：",
    ) -> CheckpointResult
        """
        暂停工作流，打印信息，等待用户输入。
        """
```

---

## 7. AgentPool — 顶层编排

```python
class AgentPool:
    def __init__(self):
        self.agents: AgentManager
        self.conversations: ConversationManager
        self.logger: Logger
        self.context: ContextManager
        self.config: Config
        self.checkpoint: Checkpoint

    def __enter__(self) -> AgentPool
        """上下文管理器入口。"""

    def __exit__(self, ...)
        """退出时停止所有 Gateway。"""
```
