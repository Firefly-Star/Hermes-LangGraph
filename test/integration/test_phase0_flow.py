"""Phase 0 集成测试：需求澄清流程。"""
from __future__ import annotations
from unittest.mock import patch
import pytest
from agent_runtime import AgentRuntime
from workflow.phase0 import PreFlightClarify


class TestPhase0Flow:
    """Phase 0 线性段：init → clarify → close。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PreFlightClarify._runtime = self.rt

    @patch("workflow.phase0.clarify_loop", return_value="用户确认完成")
    def test_full_clarify_flow(self, mock_clarify):
        """init → clarify → close 完整流程，context 正确传递。"""
        state = {}

        # Node 1: init
        state = PreFlightClarify.init(state)
        assert state["phase"] == "clarify_inject"
        master_conv = self.rt.context.get_ctx("master_conv")
        assert master_conv is not None
        assert "master" in master_conv
        # 调 Master 做系统 prompt 注入
        assert self.mock.call_history[0][0] == "master"

        # Node 2: clarify
        state = PreFlightClarify.clarify(state)
        assert state["phase"] == "clarify_close"
        assert self.rt.context.get_ctx("clarify_reason") == "用户确认完成"

        # Node 3: close — 写入 project_context.md
        state = PreFlightClarify.close(state)
        assert state["phase"] == "done"
        # 又调了一次 Master 写决策记录
        assert len(self.mock.call_history) == 2
        assert self.mock.call_history[1][0] == "master"
        assert "决策记录" in self.mock.call_history[1][2]

    @patch("workflow.phase0.clarify_loop", return_value="用户确认完成")
    def test_project_context_path_set(self, mock_clarify):
        """project_context_path 在 init 中被正确设置到 background context。"""
        PreFlightClarify.init({})
        pc_path = self.rt.context.get_bg("project_context_path")
        assert pc_path is not None
        assert "project_context.md" in pc_path

    @patch("workflow.phase0.clarify_loop", return_value="用户确认完成")
    def test_context_keys_cleared_on_init(self, mock_clarify):
        """init 应清空之前阶段残留的 context keys。"""
        # Set dirty values
        for key in ["master_reply", "pm_reply_text", "pm_reply_path",
                     "pm_criteria", "dev_conv"]:
            self.rt.context.set_ctx(key, "旧值残留")

        PreFlightClarify.init({})

        for key in ["master_reply", "pm_reply_text", "pm_reply_path",
                     "pm_criteria", "dev_conv"]:
            assert self.rt.context.get_ctx(key) == ""

    @patch("workflow.phase0.clarify_loop", return_value="用户确认完成")
    def test_close_writes_decision_to_master_conv(self, mock_clarify):
        """close 阶段通过 master conv 写决策记录。"""
        PreFlightClarify.init({})
        master_conv = self.rt.context.get_ctx("master_conv")
        PreFlightClarify.close({})
        # close 调用在同一个 master 对话
        assert self.mock.call_history[1][1] == master_conv

    @patch("workflow.phase0.clarify_loop", return_value="用户触发中断")
    def test_clarify_reason_from_loop(self, mock_clarify):
        """clarify_loop 返回的原因存入 context。"""
        PreFlightClarify.init({})
        PreFlightClarify.clarify({})
        assert self.rt.context.get_ctx("clarify_reason") == "用户触发中断"
