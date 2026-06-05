"""Phase 0 节点测试：PreFlightClarify (init / clarify / close)。"""
from __future__ import annotations
from unittest.mock import patch
import pytest
from agent_runtime import AgentRuntime
from workflow.phase0 import PreFlightClarify


class TestPreFlightInit:
    """init — Type A（纯 call_agent）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=None, conversation_client=mock_client)
        PreFlightClarify._runtime = self.rt

    def test_calls_master_agent(self):
        PreFlightClarify.init({})
        assert len(self.mock.call_history) == 1
        assert self.mock.call_history[0][0] == "master"

    def test_sets_master_conv_in_context(self):
        PreFlightClarify.init({})
        conv = self.rt.context.get_ctx("master_conv")
        assert conv is not None
        assert conv.startswith("master-")

    def test_returns_clarify_inject_phase(self):
        result = PreFlightClarify.init({})
        assert result["phase"] == "clarify_inject"

    def test_clears_context_keys(self):
        """确认旧的上下文 key 被清空。"""
        self.rt.context.set_ctx("master_reply", "旧值")
        PreFlightClarify.init({})
        assert self.rt.context.get_ctx("master_reply") == ""


class TestPreFlightClarify:
    """clarify — 调用 clarify_loop，结果存入 context。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=None, conversation_client=mock_client)
        PreFlightClarify._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.phase0.clarify_loop", return_value="用户直接确认")
    def test_stores_clarify_reason(self, mock_loop):
        PreFlightClarify.clarify({})
        assert self.rt.context.get_ctx("clarify_reason") == "用户直接确认"

    @patch("workflow.phase0.clarify_loop", return_value="用户直接确认")
    def test_returns_clarify_close_phase(self, mock_loop):
        result = PreFlightClarify.clarify({})
        assert result["phase"] == "clarify_close"

    @patch("workflow.phase0.clarify_loop", return_value="")
    def test_handles_empty_reason(self, mock_loop):
        PreFlightClarify.clarify({})
        assert self.rt.context.get_ctx("clarify_reason") == ""
        # 即使 clarify_loop 返回空字符串，state 也不应受影响
        assert not self.rt.context.get_ctx("clarify_reason")


class TestPreFlightClose:
    """close — Type A（纯 call_agent）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=None, conversation_client=mock_client)
        PreFlightClarify._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_ctx("clarify_reason", "确认完毕")

    def test_calls_master_agent(self):
        PreFlightClarify.close({})
        assert len(self.mock.call_history) == 1
        assert self.mock.call_history[0][0] == "master"

    def test_prompt_contains_project_context_path(self):
        PreFlightClarify.close({})
        prompt = self.mock.call_history[0][2]
        assert "project_context.md" in prompt

    def test_returns_done_phase(self):
        result = PreFlightClarify.close({})
        assert result["phase"] == "done"

    def test_sets_clarification_background(self):
        PreFlightClarify.close({})
        bg = self.rt.context.get_bg("clarification")
        assert bg is not None
        assert "project_context.md" in str(bg)
