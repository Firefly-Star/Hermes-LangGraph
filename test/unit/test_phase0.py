"""Phase 0 节点测试：PreFlightClarify (init / clarify / close)。"""
from __future__ import annotations
from unittest.mock import patch
import pytest
from agent_runtime import AgentRuntime
from workflow.phase0 import PreFlightClarify


class TestPreFlightInit:
    """init — Type A（纯 call_agent）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PreFlightClarify._runtime = self.rt

    def test_calls_master_agent(self):
        """init 应当调 Master agent 注入 system prompt。"""
        PreFlightClarify.init({})
        assert len(self.mock.call_history) == 1
        assert self.mock.call_history[0][0] == "master"

    def test_sets_master_conv_in_context(self):
        """init 应当在 context 中记录新建的 master conversation 名。"""
        PreFlightClarify.init({})
        conv = self.rt.context.get_ctx("master_conv")
        assert conv is not None
        assert conv.startswith("master-")

    def test_returns_clarify_inject_phase(self):
        """init 完成后 state.phase 应为 clarify_inject，指向下一个澄清节点。"""
        result = PreFlightClarify.init({})
        assert result["phase"] == "clarify_inject"

    def test_clears_context_keys(self):
        """init 应清空上一轮运行的残留 context，避免跨运行污染。"""
        self.rt.context.set_ctx("master_reply", "旧值")
        PreFlightClarify.init({})
        assert self.rt.context.get_ctx("master_reply") == ""


class TestPreFlightClarify:
    """clarify — 调用 clarify_loop，结果存入 context。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PreFlightClarify._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.phase0.clarify_loop", return_value="用户直接确认")
    def test_stores_clarify_reason(self, mock_loop):
        """clarify 应将 clarify_loop 的结束原因存入 context。"""
        PreFlightClarify.clarify({})
        assert self.rt.context.get_ctx("clarify_reason") == "用户直接确认"

    @patch("workflow.phase0.clarify_loop", return_value="用户直接确认")
    def test_returns_clarify_close_phase(self, mock_loop):
        """clarify 完成后 state.phase 应为 clarify_close。"""
        result = PreFlightClarify.clarify({})
        assert result["phase"] == "clarify_close"

    @patch("workflow.phase0.clarify_loop", return_value="")
    def test_handles_empty_reason(self, mock_loop):
        """clarify_loop 返回空字符串时，context 也应正确存储空值。"""
        PreFlightClarify.clarify({})
        assert self.rt.context.get_ctx("clarify_reason") == ""


class TestPreFlightClose:
    """close — Type A（纯 call_agent）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PreFlightClarify._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_ctx("clarify_reason", "确认完毕")

    def test_calls_master_agent(self):
        """close 应当调 Master agent 撰写 project_context.md。"""
        PreFlightClarify.close({})
        assert len(self.mock.call_history) == 1
        assert self.mock.call_history[0][0] == "master"

    def test_prompt_contains_project_context_path(self):
        """prompt 中应包含 project_context.md 的完整路径，供 agent 写入。"""
        PreFlightClarify.close({})
        prompt = self.mock.call_history[0][2]
        assert "project_context.md" in prompt

    def test_returns_done_phase(self):
        """close 完成后 state.phase 应为 done，表示 Phase 0 结束。"""
        result = PreFlightClarify.close({})
        assert result["phase"] == "done"

    def test_sets_clarification_background(self):
        """close 应将澄清结果存入 context background，供后续 phase 读取。"""
        PreFlightClarify.close({})
        bg = self.rt.context.get_bg("clarification")
        assert bg is not None
        assert "project_context.md" in str(bg)
