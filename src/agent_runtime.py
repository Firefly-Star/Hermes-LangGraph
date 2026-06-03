"""
AgentRuntime — AI Coding 工作流框架
================================


模块：
  AgentManager         — Agent 注册、Gateway 生命周期
  ConversationManager  — 对话调用、初始化、关闭
  Logger               — 调用日志、事件日志
  ContextManager       — 状态、分层计划、上下文组装
  Config               — 配置读写
  Checkpoint           — 人工检查点
  AgentRuntime          — 顶层编排，聚合全部模块
"""

import json, os, time, subprocess, requests
from dataclasses import dataclass, field, asdict
from typing import Optional, Any

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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_clean_surrogates(data), f, indent=2, ensure_ascii=False)

def _clean_surrogates(obj):
    """递归清除 dict/list 中字符串的非法代理对字符。"""
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: _clean_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_surrogates(v) for v in obj]
    return obj

def _append_jsonl(path: str, record: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(_clean_surrogates(record), ensure_ascii=False) + "\n")

# ============================================================
# 结果类型
# ============================================================

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
    """Agent registry CRUD。不管理进程。"""

    def __init__(self, runtime_dir: str, hermes_home: str):
        self._runtime_dir = runtime_dir
        self._hermes_home = hermes_home
        self._registry_path = os.path.join(runtime_dir, "registry.json")
        self._data = _read_json(self._registry_path)

    def _save(self):
        _write_json(self._registry_path, self._data)

    def _profile_path(self, profile: str) -> str:
        return os.path.join(self._hermes_home, "profiles", profile)

    def _profile_exists(self, profile: str) -> bool:
        return os.path.isdir(self._profile_path(profile))

    def _create_profile(self, profile: str, source: str = "cg"):
        hermes_cli = os.path.join(self._hermes_home, "hermes-agent", "venv", "Scripts", "hermes")
        subprocess.run(
            [hermes_cli, "profile", "create", profile, "--clone-from", source],
            capture_output=True, timeout=30,
        )

    def _write_env(self, profile: str, port: int, api_key: str):
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

    # ── 公开接口 ──────────────────────────────────────

    def register(self, name: str, profile: str, port: int, api_key: str = "kaguya"):
        """注册 agent 到 registry，初始化为 stopped。覆盖已有条目。"""
        if not self._profile_exists(profile):
            self._create_profile(profile)
        self._write_env(profile, port, api_key)
        self._data.setdefault("agents", {})[name] = {
            "profile": profile,
            "port": port,
            "api_key": api_key,
            "status": "stopped",
            "pid": None,
            "conversations": [],
        }
        self._save()

    def set_port_status(self, port: int, status: str):
        """更新同一端口所有 agent 的状态。"""
        for a in self._data.get("agents", {}).values():
            if a["port"] == port:
                a["status"] = status
        self._save()

    def set_pid(self, name: str, pid: int):
        cfg = self._data.get("agents", {}).get(name)
        if cfg:
            cfg["pid"] = pid
            self._save()

    def drop_agent(self, agent: str) -> DropAgentResult:
        self._data.get("agents", {}).pop(agent, None)
        self._save()
        return DropAgentResult(True, f"agent {agent} 已删除")

    def list_agents(self) -> list[dict]:
        agents = self._data.get("agents", {})
        return [
            {"name": n, "profile": c["profile"], "port": c["port"],
             "status": c["status"], "conversations": list(c.get("conversations", []))}
            for n, c in agents.items()
        ]

    def get_config(self, agent: str) -> Optional[dict]:
        return self._data.get("agents", {}).get(agent)


class GatewayManager:
    """Gateway 进程生命周期和端口检测。不碰 registry。"""

    def __init__(self, hermes_home: str):
        self._hermes_home = hermes_home

    def _hermes_cli(self) -> str:
        return os.path.join(self._hermes_home, "hermes-agent", "venv", "Scripts", "hermes")

    def health(self, port: int) -> bool:
        try:
            r = requests.get(f"http://127.0.0.1:{port}/health", timeout=3)
            return r.status_code == 200
        except:
            return False

    def detect(self, port: int, api_key: str, expected_profile: str) -> tuple[str, str]:
        """检测端口上的 Gateway 是否可用。返回 ("running"|"stopped", detail)。"""
        try:
            r = requests.get(f"http://127.0.0.1:{port}/health", timeout=3)
            if r.status_code != 200:
                return "stopped", f"health 返回 {r.status_code}"
            body = r.json()
            if body.get("platform") != "hermes-agent":
                return "stopped", "不是 Hermes Gateway"
            try:
                r2 = requests.post(
                    f"http://127.0.0.1:{port}/v1/responses",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"input": "ping", "conversation": f"verify-{int(time.time())}"},
                    timeout=10,
                )
                if r2.status_code == 200:
                    actual = r2.json().get("model", "?")
                    if actual == expected_profile:
                        return "running", f"profile=✓({actual})"
                    else:
                        return "stopped", f"profile=✗(期望{expected_profile}, 实际{actual})"
            except:
                pass
            return "running", "health 确认 Hermes Gateway"
        except requests.ConnectionError:
            return "stopped", "端口无响应"
        except Exception as e:
            return "stopped", f"检测异常: {e}"

    def run(self, profile: str, port: int, api_key: str) -> tuple[bool, str, Optional[int]]:
        """启动 Gateway 进程，等待就绪。返回 (success, message, pid)。"""
        env = os.environ.copy()
        env["API_SERVER_PORT"] = str(port)
        env["API_SERVER_ENABLED"] = "true"
        env["API_SERVER_KEY"] = api_key
        cmd = [self._hermes_cli(), "--profile", profile, "gateway", "run"]
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        for _ in range(30):
            time.sleep(1)
            try:
                r = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
                if r.status_code == 200:
                    return True, f"就绪 (PID={proc.pid})", proc.pid
            except:
                pass
        return False, "启动超时", None

    def stop(self, pid: int):
        """强制停止进程。"""
        if not pid:
            return
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        except:
            pass


# ============================================================
# 2. ConversationManager
# ============================================================

def _resolve_file_refs(text: str) -> str:
    """将 prompt 中的 {文件路径} 替换为文件内容，非文件路径保留原样。"""
    import re
    def _replacer(m):
        path = m.group(1)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return m.group(0)
        return m.group(0)
    return re.sub(r'\{([^}]+)\}', _replacer, text)


class ConversationManager:
    """对话调用、初始化、关闭。"""

    def __init__(self, agent_mgr: AgentManager, logger: "Logger", config: "Config", runtime_dir: str):
        self._agents = agent_mgr
        self._logger = logger
        self._config = config
        self._registry_path = os.path.join(runtime_dir, "registry.json")

    def call(self, agent: str, conversation: str, input_text: str,
             timeout: int = None, stream_callback: callable = None,
             tool_callback: callable = None) -> CallResult:
        cfg = self._agents.get_config(agent)
        if not cfg:
            return CallResult(False, "", error=f"agent {agent} 不存在")
        if cfg["status"] != "running":
            return CallResult(False, "", error=f"{agent} gateway 未运行")
        timeout = timeout or self._config.get("call_timeout")
        port = cfg["port"]
        api_key = cfg.get("api_key", "kaguya")
        t0 = time.time()
        input_text = _resolve_file_refs(input_text)

        if stream_callback:
            return self._call_stream(agent, conversation, input_text, port, api_key, timeout, stream_callback, t0, tool_callback)

        max_retry = self._config.get("max_retry")
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

    def close(self, agent: str, conversation: str):
        """close_conversation 的别名。"""
        return self.close_conversation(agent, conversation)

    def close_conversation(self, agent: str, conversation: str):
        """停止追踪指定对话。不清除服务端数据。"""
        data = _read_json(self._registry_path)
        cfg = data.get("agents", {}).get(agent)
        if cfg and conversation in cfg.get("conversations", []):
            cfg["conversations"].remove(conversation)
            _write_json(self._registry_path, data)

    def _track_conversation(self, agent: str, conversation: str):
        """将 conversation 加入 registry 的追踪列表。"""
        data = _read_json(self._registry_path)
        cfg = data.get("agents", {}).get(agent)
        if cfg:
            convs = cfg.setdefault("conversations", [])
            if conversation not in convs:
                convs.append(conversation)
                _write_json(self._registry_path, data)

    def _call_stream(self, agent, conversation, input_text, port, api_key, timeout, callback, t0, tool_callback=None):
        """流式调用 Hermes Gateway。timeout 只约束连接和首块到达时间。"""
        try:
            resp = requests.post(
                f"http://127.0.0.1:{port}/v1/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"input": input_text, "conversation": conversation, "stream": True},
                stream=True, timeout=(timeout, None),
            )
        except Exception as e:
            return CallResult(False, "", error=f"连接失败: {e}", latency_ms=int((time.time() - t0) * 1000))
        if resp.status_code != 200:
            latency = int((time.time() - t0) * 1000)
            return CallResult(False, "", error=f"HTTP {resp.status_code}", latency_ms=latency)

        text_parts = []
        raw_data = None
        error_msg = None

        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                et = data.get("type")

                if et == "response.output_text.delta":
                    txt = data.get("delta", "")
                    if txt:
                        text_parts.append(txt)
                        callback(txt)
                elif et == "response.output_item.added":
                    item = data.get("item", {})
                    if item.get("type") == "function_call":
                        if tool_callback:
                            name = item.get("name", "")
                            args = item.get("arguments", "{}")
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {"raw": args}
                            tool_callback(name, args)
                elif et == "response.completed":
                    raw_data = data.get("response", {})
                    break
                elif et == "response.error":
                    error_msg = data.get("error", {}).get("message", "流式错误")
                    break
        finally:
            resp.close()

        latency = int((time.time() - t0) * 1000)

        if raw_data:
            full_text = "".join(text_parts)
            usage = raw_data.get("usage", {})
            self._logger.log_call(
                agent=agent, conversation=conversation,
                input_text=input_text, output_text=full_text,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                latency_ms=latency, success=True,
            )
            self._track_conversation(agent, conversation)
            return CallResult(
                True, full_text,
                usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                latency, raw_data=raw_data,
            )

        return CallResult(False, "", error=error_msg or "流式响应未完成", latency_ms=latency)


# ============================================================
# 3. Logger
# ============================================================

class Logger:
    """调用日志、事件日志。"""

    def __init__(self, runtime_dir: str):
        self._calls_path = os.path.join(runtime_dir, "calls.jsonl")
        self._events_path = os.path.join(runtime_dir, "events.jsonl")

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
        _append_jsonl(self._calls_path, record)

    def log_event(self, event_type: str, agent: str = None, detail: str = None):
        record = {
            "timestamp": _iso_now(),
            "event_type": event_type,
            "agent": agent,
            "detail": detail,
        }
        _append_jsonl(self._events_path, record)

    def get_calls(self, agent: str = None, conversation: str = None, limit: int = 50) -> list[dict]:
        if not os.path.exists(self._calls_path):
            return []
        result = []
        with open(self._calls_path, "r", encoding="utf-8") as f:
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
        if not os.path.exists(self._events_path):
            return []
        result = []
        with open(self._events_path, "r", encoding="utf-8") as f:
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

    def __init__(self, runtime_dir: str):
        self._context_path = os.path.join(runtime_dir, "context.json")
        self._data = _read_json(self._context_path)

    def _save(self):
        _write_json(self._context_path, self._data)

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
    """配置读写。直接读写 runtime_config.json。"""

    DEFAULTS = {
        "call_timeout": 120,
        "max_retry": 3,
        "max_plan_loop": 5,
        "max_bug_loop": 5,
    }

    def __init__(self, config_path: str):
        self._config_path = config_path
        self._data = _read_json(config_path) if os.path.exists(config_path) else {}
        self._flatten_sections()

    def _flatten_sections(self):
        """将 paths/limits/interaction 等分节的值提升到顶层，兼容扁平 key 访问。"""
        for section in ("paths", "limits", "interaction", "dirs"):
            if section in self._data and isinstance(self._data[section], dict):
                for k, v in self._data[section].items():
                    if k not in self._data:
                        self._data[k] = v

    def _save(self):
        _write_json(self._config_path, self._data)

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
             prompt: str = "确认无误请按 Enter 继续，或输入修改意见：",
             end_word: str = None) -> CheckpointResult:
        print(f"\n{'='*50}")
        print(f"【{title}】")
        print(f"{'='*50}")
        print(content)
        print(f"\n{prompt}", end=" ", flush=True)

        if end_word:
            print(f"（输入 {end_word} 结束多行输入）")
            lines = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if line.strip() == end_word:
                    break
                lines.append(line)
            user_input = "\n".join(lines).strip()
        else:
            user_input = input().strip()

        if not user_input:
            return CheckpointResult("continue", "")
        if user_input.lower() in ("reject", "退回"):
            return CheckpointResult("reject", "")
        return CheckpointResult("modify", user_input)


# ============================================================
# 7. AgentRuntime — 顶层编排
# ============================================================

class AgentRuntime:
    """聚合全部模块，提供统一入口。可通过 config_path 加载配置。"""

    def __init__(self, config_path: str = None):
        cfg = self._load_config(config_path) if config_path else {}
        paths_cfg = cfg.get("paths", {})
        runtime_dir = paths_cfg.get("runtime_dir") or cfg.get("runtime_dir") or cfg.get("pool_dir", ".agent_runtime")
        hermes_home = paths_cfg.get("hermes_home") or cfg.get("hermes_home", self._detect_hermes_home())
        workspace = paths_cfg.get("workspace") or cfg.get("workspace") or os.getcwd()

        os.makedirs(runtime_dir, exist_ok=True)

        self.runtime_dir = runtime_dir
        self.workspace = workspace
        self.config = Config(config_path or os.path.join(os.getcwd(), "runtime_config.json"))
        self.logger = Logger(runtime_dir)
        self._hermes_home = hermes_home
        self.agents = AgentManager(runtime_dir, hermes_home)
        self._gateway = GatewayManager(hermes_home)
        self.context = ContextManager(runtime_dir)
        self.conversations = ConversationManager(self.agents, self.logger, self.config, runtime_dir)
        self.checkpoint = Checkpoint()

    def run_all(self, configs: dict):
        """注册所有 agent，逐端口检测/启动 gateway。"""
        for name, cfg in configs.items():
            self.agents.register(name, cfg["profile"], cfg["port"],
                                api_key=cfg.get("api_key", "kaguya"))

        ports = {}
        for name, cfg in configs.items():
            port = cfg["port"]
            ports.setdefault(port, []).append(name)

        for port, names in ports.items():
            if self._gateway.health(port):
                status, detail = self._gateway.detect(port, "kaguya", configs[names[0]]["profile"])
                if status != "running":
                    raise RuntimeError(f"端口 {port} 已被占用但不是 Hermes Gateway: {detail}")
                self.agents.set_port_status(port, "running")
                print(f"  {', '.join(names)} gateway 就绪（已有）")
            else:
                profile = configs[names[0]]["profile"]
                ok, msg, pid = self._gateway.run(profile, port, "kaguya")
                if not ok:
                    raise RuntimeError(f"启动 {names[0]} gateway 失败: {msg}")
                self.agents.set_port_status(port, "running")
                for n in names:
                    self.agents.set_pid(n, pid)
                print(f"  {', '.join(names)} gateway 就绪")

    @staticmethod
    def _load_config(config_path: str) -> dict:
        """读取 JSON 配置文件。"""
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _detect_hermes_home() -> str:
        """尝试检测 Hermes 安装目录。"""
        candidates = [
            os.path.expanduser("~/AppData/Local/hermes"),
            r"C:\Users\温学周\AppData\Local\hermes",
            r"C:\Program Files\hermes",
        ]
        for path in candidates:
            if os.path.isdir(os.path.join(path, "profiles")):
                return path
        # 兜底：让用户自己配
        return os.path.expanduser("~/AppData/Local/hermes")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for a in self.agents.list_agents():
            if a["status"] == "running":
                self._gateway.stop(a.get("pid"))
        return False
