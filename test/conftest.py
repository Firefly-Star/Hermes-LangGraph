"""MockClient + pytest fixtures。"""
from __future__ import annotations
import json, os, sys

# 确保 src/ 在 Python path 中
_src = os.path.join(os.path.dirname(__file__), "..", "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

# GBK 终端下 ✓ 等字符会报错，全局替换
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import pytest
from agent_runtime import CallResult, ConversationClient


class MockClient(ConversationClient):
    """对话测试桩 — 按预设文本回复，不连 Hermes Gateway。"""

    def __init__(self):
        self.call_history: list[tuple[str, str, str]] = []
        self.responses: dict[str, str] = {}

    def set_response(self, prompt_start: str, reply: str):
        self.responses[prompt_start] = reply

    def call(self, agent, conversation, input_text, timeout=None,
             stream_callback=None, tool_callback=None,
             poll_callback=None) -> CallResult:
        self.call_history.append((agent, conversation, input_text))
        # 按 prompt 最长前缀匹配（最具体的优先）
        text = "默认 mock 回复"
        match_len = -1
        for key, reply in self.responses.items():
            if input_text.startswith(key) and len(key) > match_len:
                match_len = len(key)
                text = reply
        return CallResult(True, text, 0, 0, 0)

    def close(self, agent, conversation):
        self.call_history.append((f"close:{agent}", conversation, ""))


@pytest.fixture
def test_config(tmp_path):
    """生成隔离的测试用 config.json，所有路径指向 tmp_path。"""
    p = tmp_path / ".agent_runtime"
    cfg = {
        "paths": {
            "runtime_dir": str(p),
            "workspace": str(tmp_path / "workspace"),
            "handoffs": str(p / "handoffs"),
            "phases": str(p / "phases"),
            "artifacts": str(p / "artifacts"),
            "checkpoint": str(p / "checkpoint.json"),
        },
        "fail_rollback_threshold": 3,
        "fail_escalation_threshold": 5,
    }
    config_path = tmp_path / "test_config.json"
    json.dump(cfg, open(config_path, "w", encoding="utf-8"))
    return str(config_path)


class InteractiveClient(ConversationClient):
    """交互式测试桩 — 替换 ConversationClient.call()，每次调用让用户手动输入回复。

    call_agent 的日志/计时/中断机制均不受影响，仅接管 LLM 调用层。

    用法:
        client = InteractiveClient()
        runtime = AgentRuntime(config_path=config, conversation_client=client)
        # 运行节点，每遇到 runtime.conversations.call() 会等待输入
    """

    def __init__(self, default_reply: str = "默认 mock 回复"):
        self.call_history: list[tuple[str, str, str]] = []
        self.default_reply = default_reply
        self.responses: dict[str, str] = {}

    def set_response(self, prompt_start: str, reply: str):
        self.responses[prompt_start] = reply

    def call(self, agent, conversation, input_text, timeout=None,
             stream_callback=None, tool_callback=None,
             poll_callback=None) -> CallResult:
        self.call_history.append((agent, conversation, input_text))

        # 先查预设回复
        text = ""
        match_len = -1
        for key, reply in self.responses.items():
            if input_text.startswith(key) and len(key) > match_len:
                match_len = len(key)
                text = reply
        if text:
            return CallResult(True, text, 0, 0, 0)

        # 交互输入
        print(f"\n[交互输入] {agent} | {conversation}")
        print("输入回复，空行结束。↓")

        user_lines = []
        while True:
            try:
                line = input("> ")
                if not line:
                    break
                user_lines.append(line)
            except (EOFError, KeyboardInterrupt):
                break

        reply = "\n".join(user_lines) or self.default_reply
        return CallResult(True, reply, 0, 0, 0)

    def close(self, agent, conversation):
        self.call_history.append((f"close:{agent}", conversation, ""))


@pytest.fixture
def mock_client():
    return MockClient()
