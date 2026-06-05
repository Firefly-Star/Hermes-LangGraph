"""MockClient + pytest fixtures。"""
from __future__ import annotations
import os, sys

# 确保 src/ 在 Python path 中
_src = os.path.join(os.path.dirname(__file__), "..", "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

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
        # 按 prompt 前缀匹配
        prefix = input_text.strip()[:80]
        text = "默认 mock 回复"
        for key, reply in self.responses.items():
            if input_text.startswith(key):
                text = reply
                break
        return CallResult(True, text, 0, 0, 0)

    def close(self, agent, conversation):
        self.call_history.append((f"close:{agent}", conversation, ""))


@pytest.fixture
def mock_client():
    return MockClient()
