"""
Agent Pool — AI Coding 工作流框架
================================
严格遵循 detail-design-v1.md 的功能边界与功能范围设计。

模块：
  AgentManager         — Agent 注册、Gateway 生命周期
  ConversationManager  — 对话调用、初始化、关闭
  Logger               — 调用日志、事件日志
  ContextManager       — 状态、分层计划、上下文组装
  Config               — 配置读写
  Checkpoint           — 人工检查点
  AgentPool            — 顶层编排，聚合全部模块
"""

import json, os, time, subprocess, requests
from dataclasses import dataclass, field, asdict
from typing import Optional, Any

# ============================================================
# 路径常量
# ============================================================
HERE = os.path.dirname(os.path.abspath(__file__))
POOL_DIR = os.path.join(HERE, ".agent_pool")
REGISTRY_PATH = os.path.join(POOL_DIR, "registry.json")
CONTEXT_PATH = os.path.join(POOL_DIR, "context.json")
CONFIG_PATH = os.path.join(POOL_DIR, "config.json")
CALLS_LOG_PATH = os.path.join(POOL_DIR, "calls.jsonl")
EVENTS_LOG_PATH = os.path.join(POOL_DIR, "events.jsonl")

HERMES_CLI = r"C:\Users\温学周\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes"
HERMES_HOME = r"C:\Users\温学周\AppData\Local\hermes"

def _ensure_dir():
    os.makedirs(POOL_DIR, exist_ok=True)

# ============================================================
# 工具函数
# ============================================================

def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

def _read_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_json(path: str, data: dict):
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _append_jsonl(path: str, record: dict):
    _ensure_dir()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

# ============================================================
# 结果类型
# ============================================================

@dataclass
class CreateAgentResult:
    success: bool
    message: str
    status: str          # "running" | "stopped"

@dataclass
class RunGatewayResult:
    success: bool
    message: str
    pid: Optional[int] = None

@dataclass
class StopGatewayResult:
    success: bool
    message: str

@dataclass
class DropAgentResult:
    success: bool
    message: str

@dataclass
class CallResult:
    success: bool
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: Optional[str] = None
    raw_data: Optional[dict] = None        # Hermes 返回的完整 JSON

@dataclass
class CheckpointResult:
    action: str           # "continue" | "modify" | "reject"
    message: str

@dataclass
class ProgressReport:
    project_info: dict = field(default_factory=dict)
    current_phase: str = ""
    completed_steps: list = field(default_factory=list)
    plans: dict = field(default_factory=dict)
    current_position: dict = field(default_factory=dict)
    contexts: dict = field(default_factory=dict)


# ============================================================
# 1. AgentManager
# ============================================================

class AgentManager:
    """Agent 注册、Gateway 生命周期管理。"""

    def __init__(self):
        self._data = _read_json(REGISTRY_PATH)

    def _save(self):
        _write_json(REGISTRY_PATH, self._data)

    def _profile_path(self, profile: str) -> str:
        return os.path.join(HERMES_HOME, "profiles", profile)

    def _profile_exists(self, profile: str) -> bool:
        return os.path.isdir(self._profile_path(profile))

    def _create_profile(self, profile: str, source: str = "cg"):
        """创建 Hermes Profile。"""
        subprocess.run(
            [HERMES_CLI, "profile", "create", profile, "--clone-from", source],
            capture_output=True, timeout=30,
        )

    def _write_env(self, profile: str, port: int, api_key: str):
        """写 profile 的 .env 文件。"""
        env_path = os.path.join(self._profile_path(profile), ".env")
        os.makedirs(os.path.dirname(env_path), exist_ok=True)
        existing = {}
        if os.path.exists(env_path):
            for line in open(env_path, "r", encoding="utf-8"):
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    existing[k] = v
        existing["API_SERVER_ENABLED"] = "true"
        existing["API_SERVER_KEY"] = api_key
        existing["API_SERVER_PORT"] = str(port)
        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in existing.items():
                f.write(f"{k}={v}\n")

    def _detect_gateway(self, port: int, api_key: str, expected_profile: str) -> tuple[str, str]:
        """
        检测端口上的 Gateway，返回 (status, detail)。
        status: "running" | "stopped"
        """
        try:
            r = requests.get(f"http://127.0.0.1:{port}/health", timeout=3)
            if r.status_code != 200:
                return "stopped", f"health 返回 {r.status_code}"
            body = r.json()
            if body.get("platform") != "hermes-agent":
                return "stopped", "不是 Hermes Gateway"
            r2 = requests.post(
                f"http://127.0.0.1:{port}/v1/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"input": "ping", "conversation": "__verify__"},
                timeout=10,
            )
            if r2.status_code != 200:
                return "stopped", f"API 返回 {r2.status_code}"
            actual = r2.json().get("model", "?")
            if actual == expected_profile:
                return "running", f"profile=✓({actual})"
            else:
                return "stopped", f"profile=✗(期望{expected_profile}, 实际{actual})"
        except requests.ConnectionError:
            return "stopped", "端口无响应"
        except Exception as e:
            return "stopped", f"检测异常: {e}"

    # ── 公开接口 ──────────────────────────────────────

    def create_agent(self, name: str, profile: str, port: int, api_key: str = "kaguya") -> CreateAgentResult:
        if name in self._data.get("agents", {}):
            return CreateAgentResult(False, f"agent {name} 已存在", "stopped")
        # 创建 profile
        if not self._profile_exists(profile):
            self._create_profile(profile)
        self._write_env(profile, port, api_key)
        # 检测 Gateway
        status, detail = self._detect_gateway(port, api_key, profile)
        # 写入 registry
        self._data.setdefault("agents", {})[name] = {
            "profile": profile,
            "port": port,
            "api_key": api_key,
            "status": status,
            "pid": None,
            "conversations": [],
        }
        self._save()
        return CreateAgentResult(True, detail, status)

    def run_gateway(self, agent: str) -> RunGatewayResult:
        cfg = self._data.get("agents", {}).get(agent)
        if not cfg:
            return RunGatewayResult(False, f"agent {agent} 不存在")
        if cfg["status"] == "running":
            return RunGatewayResult(True, f"已在运行", cfg.get("pid"))
        env = os.environ.copy()
        env["API_SERVER_PORT"] = str(cfg["port"])
        env["API_SERVER_ENABLED"] = "true"
        env["API_SERVER_KEY"] = cfg.get("api_key", "kaguya")
        cmd = [HERMES_CLI, "--profile", cfg["profile"], "gateway", "run"]
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        for _ in range(30):
            time.sleep(1)
            try:
                r = requests.get(f"http://127.0.0.1:{cfg['port']}/health", timeout=2)
                if r.status_code == 200:
                    cfg["status"] = "running"
                    cfg["pid"] = proc.pid
                    self._save()
                    return RunGatewayResult(True, f"就绪 (PID={proc.pid})", proc.pid)
            except:
                pass
        return RunGatewayResult(False, "启动超时")

    def stop_gateway(self, agent: str) -> StopGatewayResult:
        cfg = self._data.get("agents", {}).get(agent)
        if not cfg:
            return StopGatewayResult(False, f"agent {agent} 不存在")
        if cfg["status"] != "running":
            return StopGatewayResult(True, "未运行")
        pid = cfg.get("pid")
        if pid:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
            except:
                pass
        cfg["status"] = "stopped"
        cfg["pid"] = None
        cfg["conversations"] = []
        self._save()
        return StopGatewayResult(True, "已停止")

    def drop_agent(self, agent: str) -> DropAgentResult:
        self.stop_gateway(agent)
        self._data.get("agents", {}).pop(agent, None)
        self._save()
        return DropAgentResult(True, f"agent {agent} 已删除")

    def health(self, agent: str) -> bool:
        cfg = self._data.get("agents", {}).get(agent)
        if not cfg:
            return False
        try:
            r = requests.get(f"http://127.0.0.1:{cfg['port']}/health", timeout=3)
            return r.status_code == 200
        except:
            return False

    def list_agents(self) -> list[dict]:
        agents = self._data.get("agents", {})
        return [
            {"name": n, "profile": c["profile"], "port": c["port"],
             "status": c["status"], "conversations": list(c.get("conversations", []))}
            for n, c in agents.items()
        ]

    def get_config(self, agent: str) -> Optional[dict]:
        return self._data.get("agents", {}).get(agent)


# ============================================================
# 2. ConversationManager
# ============================================================

class ConversationManager:
    """对话调用、初始化、关闭。"""

    def __init__(self, agent_mgr: AgentManager, logger: "Logger", config: "Config"):
        self._agents = agent_mgr
        self._logger = logger
        self._config = config

    def call(self, agent: str, conversation: str, input_text: str,
             timeout: int = None) -> CallResult:
        cfg = self._agents.get_config(agent)
        if not cfg:
            return CallResult(False, "", error=f"agent {agent} 不存在")
        if cfg["status"] != "running":
            return CallResult(False, "", error=f"{agent} gateway 未运行")
        timeout = timeout or self._config.get("call_timeout")
        max_retry = self._config.get("max_retry")
        port = cfg["port"]
        api_key = cfg.get("api_key", "kaguya")
        t0 = time.time()
        last_error = None
        for attempt in range(1 + max_retry):
            try:
                resp = requests.post(
                    f"http://127.0.0.1:{port}/v1/responses",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"input": input_text, "conversation": conversation},
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # 提取文本（兼容旧代码）
                    text = ""
                    for msg in data.get("output", []):
                        if msg.get("type") == "message":
                            for c in msg.get("content", []):
                                if c.get("type") == "output_text":
                                    text += c.get("text", "")
                    usage = data.get("usage", {})
                    latency = int((time.time() - t0) * 1000)
                    it = usage.get("input_tokens", 0)
                    ot = usage.get("output_tokens", 0)
                    # 记录调用日志
                    self._logger.log_call(
                        agent=agent, conversation=conversation,
                        input_text=input_text, output_text=text,
                        input_tokens=it, output_tokens=ot, latency_ms=latency,
                        success=True,
                    )
                    # 追踪 conversation 到 registry
                    self._track_conversation(agent, conversation)
                    return CallResult(True, text, it, ot, latency, raw_data=data)
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except requests.Timeout:
                last_error = "超时"
                self._logger.log_event("call_retried", agent, f"{agent}/{conversation} 第{attempt+1}次超时")
            except Exception as e:
                last_error = str(e)
            time.sleep(1)
        latency = int((time.time() - t0) * 1000)
        self._logger.log_call(
            agent=agent, conversation=conversation,
            input_text=input_text, output_text="",
            input_tokens=0, output_tokens=0, latency_ms=latency,
            success=False, error=last_error,
        )
        return CallResult(False, "", error=f"重试{max_retry}次失败: {last_error}")

    def init_conversation(self, agent: str, conversation: str, initial_prompt: str) -> CallResult:
        """初始化一个对话。如果已存在同名对话，先关闭旧对话。"""
        self.close_conversation(agent, conversation)
        return self.call(agent, conversation, initial_prompt)

    def close_conversation(self, agent: str, conversation: str):
        """停止追踪指定对话。不清除服务端数据。"""
        data = _read_json(REGISTRY_PATH)
        cfg = data.get("agents", {}).get(agent)
        if cfg and conversation in cfg.get("conversations", []):
            cfg["conversations"].remove(conversation)
            _write_json(REGISTRY_PATH, data)

    def _track_conversation(self, agent: str, conversation: str):
        """将 conversation 加入 registry 的追踪列表。"""
        data = _read_json(REGISTRY_PATH)
        cfg = data.get("agents", {}).get(agent)
        if cfg:
            convs = cfg.setdefault("conversations", [])
            if conversation not in convs:
                convs.append(conversation)
                _write_json(REGISTRY_PATH, data)


# ============================================================
# 3. Logger
# ============================================================

class Logger:
    """调用日志、事件日志。"""

    def log_call(self, agent: str, conversation: str, input_text: str, output_text: str,
                 input_tokens: int, output_tokens: int, latency_ms: int,
                 success: bool, error: str = None):
        record = {
            "timestamp": _iso_now(),
            "agent": agent,
            "conversation": conversation,
            "input_text": input_text,
            "input_length": len(input_text),
            "output_text": output_text,
            "output_length": len(output_text),
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "success": success,
            "error": error,
        }
        _append_jsonl(CALLS_LOG_PATH, record)

    def log_event(self, event_type: str, agent: str = None, detail: str = None):
        record = {
            "timestamp": _iso_now(),
            "event_type": event_type,
            "agent": agent,
            "detail": detail,
        }
        _append_jsonl(EVENTS_LOG_PATH, record)

    def get_calls(self, agent: str = None, conversation: str = None, limit: int = 50) -> list[dict]:
        if not os.path.exists(CALLS_LOG_PATH):
            return []
        result = []
        with open(CALLS_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if agent and rec.get("agent") != agent:
                    continue
                if conversation and rec.get("conversation") != conversation:
                    continue
                result.append(rec)
        return result[-limit:]

    def get_events(self, agent: str = None, event_type: str = None, limit: int = 50) -> list[dict]:
        if not os.path.exists(EVENTS_LOG_PATH):
            return []
        result = []
        with open(EVENTS_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if agent and rec.get("agent") != agent:
                    continue
                if event_type and rec.get("event_type") != event_type:
                    continue
                result.append(rec)
        return result[-limit:]


# ============================================================
# 4. ContextManager
# ============================================================

class ContextManager:
    """三段式上下文管理：background / phase（树状） / contexts。"""

    def __init__(self):
        self._data = _read_json(CONTEXT_PATH)

    def _save(self):
        _write_json(CONTEXT_PATH, self._data)

    # ── background（项目信息，写一次就不变） ────────────

    def set_bg(self, key: str, value: str):
        self._data.setdefault("background", {})[key] = value
        self._save()

    def get_bg(self, key: str) -> str:
        return self._data.get("background", {}).get(key, "")

    # ── phase（树状 plan，只展开 wip 节点） ────────────

    def set_phase_node(self, path: list[str], status: str, title: str = None):
        """
        创建或更新一个 phase 树节点。
        path = ["顶层规划", "后端实现", "文章 CRUD"]
          → 沿路径查找/创建节点，设置 status，可选更新 title
        status: "todo" | "wip" | "done"
        """
        self._data.setdefault("phase", {"title": "", "status": "", "children": []})
        node = self._data["phase"]
        for i, name in enumerate(path):
            if i == 0:
                node["title"] = name
                if len(path) == 1:
                    node["status"] = status
                    if title:
                        node["title"] = title
                continue
            children = node.setdefault("children", [])
            found = None
            for child in children:
                if child["title"] == name:
                    found = child
                    break
            if not found:
                found = {"title": name, "status": "todo", "children": []}
                children.append(found)
            node = found
            if i == len(path) - 1:
                node["status"] = status
                if title:
                    node["title"] = title
        self._save()

    def get_phase_text(self, indent: int = 0) -> str:
        """渲染 phase 树为缩进文本。"""
        phase = self._data.get("phase")
        if not phase or not phase.get("title"):
            return ""
        return self._render_node(phase, 0)

    def _render_node(self, node: dict, depth: int) -> str:
        status = node.get("status", "")
        title = node.get("title", "")
        prefix = "  " * depth
        icon = {"done": "✓", "wip": "●", "in_progress": "◕", "todo": "○"}.get(status, "?")
        line = f"{prefix}{icon} {title}  [{status}]"
        parts = [line]
        children = node.get("children", [])
        if children and status in ("wip", "in_progress"):
            for child in children:
                parts.append(self._render_node(child, depth + 1))
        return "\n".join(parts)

    # ── contexts（文本摘要） ────────────────────────────

    def set_ctx(self, key: str, value: str):
        self._data.setdefault("contexts", {})[key] = value
        self._save()

    def get_ctx(self, key: str) -> str:
        return self._data.get("contexts", {}).get(key, "")

    # ── 注入组装 ────────────────────────────────────────

    def build_injection(self, keys: list[str]) -> str:
        parts = []
        for key in keys:
            if key == "background":
                bg = self._data.get("background", {})
                if bg:
                    parts.append("== 项目信息 ==\n" + "\n".join(f"  {k}: {v}" for k, v in bg.items()))
            elif key == "phase":
                txt = self.get_phase_text()
                if txt:
                    parts.append("== 进度 ==\n" + txt)
            else:
                val = self.get_ctx(key)
                if val:
                    parts.append(f"== {key} ==\n{val}")
        return "\n\n".join(parts)


# ============================================================
# 5. Config
# ============================================================

class Config:
    """配置读写。"""

    DEFAULTS = {
        "call_timeout": 120,
        "max_retry": 3,
        "max_plan_loop": 5,
        "max_bug_loop": 5,
    }

    def __init__(self):
        self._data = _read_json(CONFIG_PATH)

    def _save(self):
        _write_json(CONFIG_PATH, self._data)

    def set(self, key: str, value):
        self._data[key] = value
        self._save()

    def get(self, key: str):
        return self._data.get(key, self.DEFAULTS.get(key))


# ============================================================
# 6. Checkpoint
# ============================================================

class Checkpoint:
    """人工检查点。"""

    def wait(self, title: str, content: str,
             prompt: str = "确认无误请按 Enter 继续，或输入修改意见：") -> CheckpointResult:
        print(f"\n{'='*50}")
        print(f"【{title}】")
        print(f"{'='*50}")
        print(content)
        print(f"\n{prompt}", end=" ", flush=True)
        user_input = input().strip()
        if not user_input:
            return CheckpointResult("continue", "")
        if user_input.lower() in ("reject", "退回"):
            return CheckpointResult("reject", "")
        return CheckpointResult("modify", user_input)


# ============================================================
# 7. AgentPool — 顶层编排
# ============================================================

class AgentPool:
    """聚合全部模块，提供统一入口。"""

    def __init__(self):
        _ensure_dir()
        self.config = Config()
        self.logger = Logger()
        self.agents = AgentManager()
        self.context = ContextManager()
        self.conversations = ConversationManager(self.agents, self.logger, self.config)
        self.checkpoint = Checkpoint()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出时停止所有 Gateway。"""
        for a in self.agents.list_agents():
            if a["status"] == "running":
                self.agents.stop_gateway(a["name"])
        return False
