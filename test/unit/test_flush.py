"""Flush 子图节点测试：4 组 write_summary + flush_conv。"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import os
import pytest
from agent_runtime import AgentRuntime
from workflow.flush import (
    MASTER_FLUSH_CLARIFY_DEF,
    MASTER_FLUSH_PM_DEF,
    MASTER_FLUSH_DEV_DEF,
    MASTER_FLUSH_QA_DEF,
)


class TestMasterFlushQASummary:
    """master_flush_qa_summary — QA 阶段总结（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_FLUSH_QA_DEF.nodes["master_flush_qa_summary"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写总结调 Master agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_uses_master_conv(self, mock_ensure):
        """使用 master_conv 对话。"""
        self.fn({})
        assert self.mock.call_history[0][1] == "master-test"

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_prompt_contains_phase_name(self, mock_ensure):
        """prompt 包含阶段名「QA 测试」。"""
        self.fn({})
        prompt = self.mock.call_history[0][2]
        assert "QA 测试" in prompt

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_prompt_contains_artifacts(self, mock_ensure):
        """prompt 包含 QA 阶段的产出物路径。"""
        self.fn({})
        prompt = self.mock.call_history[0][2]
        assert "test-plan.md" in prompt
        assert "test-report.md" in prompt

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_returns_qa_flushed_phase(self, mock_ensure):
        """phase 应为 qa_flushed。"""
        result = self.fn({})
        assert result["phase"] == "qa_flushed"

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_stores_summary_path_in_context(self, mock_ensure):
        """phase_summary_path 应存入 context。"""
        self.fn({})
        assert self.rt.context.get_ctx("phase_summary_path") is not None

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_calls_ensure_write_file(self, mock_ensure):
        """ensure_write_file 被调用。"""
        self.fn({})
        assert mock_ensure.called

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_creates_phases_directory(self, mock_ensure):
        """phases 目录被创建。"""
        phases_dir = self.rt.paths.phases
        assert not os.path.exists(phases_dir)
        self.fn({})
        assert os.path.isdir(phases_dir)


class TestMasterFlushQAConv:
    """master_flush_qa_conv — QA 阶段 flush 对话（Type D）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_FLUSH_QA_DEF.nodes["master_flush_qa_conv"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_ctx("phase_summary_path", "/tmp/summary.md")

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_closes_master_conv(self, mock_save, mock_open):
        """关闭旧 master 对话。"""
        self.fn({})
        assert ("close:master", "master-test", "") in self.mock.call_history

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_opens_new_conv(self, mock_save, mock_open):
        """通过 open_master_conv 创建新对话。"""
        self.fn({})
        mock_open.assert_called_once_with(self.rt, "/tmp/summary.md")

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_saves_checkpoint_with_resume_node(self, mock_save, mock_open):
        """save_checkpoint 使用 resume_node=consistency_audit。"""
        self.fn({})
        mock_save.assert_called_once_with(
            self.rt, "consistency_audit", "QA 测试",
            summary_path="/tmp/summary.md")

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_returns_conv_flushed_phase(self, mock_save, mock_open):
        """phase 应为 qa_conv_flushed。"""
        result = self.fn({})
        assert result["phase"] == "qa_conv_flushed"

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_uses_phase_summary_from_context(self, mock_save, mock_open):
        """从 context 读取 phase_summary_path。"""
        self.rt.context.set_ctx("phase_summary_path", "/custom/summary.md")
        self.fn({})
        mock_open.assert_called_once_with(self.rt, "/custom/summary.md")


class TestMasterFlushClarifySummary:
    """master_flush_clarify_summary — 配置验证。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_FLUSH_CLARIFY_DEF.nodes["master_flush_clarify_summary"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_returns_clarify_flushed_phase(self, mock_ensure):
        """phase 应为 clarify_flushed。"""
        result = self.fn({})
        assert result["phase"] == "clarify_flushed"

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_prompt_contains_phase_name(self, mock_ensure):
        """prompt 包含阶段名「需求澄清」。"""
        self.fn({})
        assert "需求澄清" in self.mock.call_history[0][2]

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_prompt_says_next_step_pm(self, mock_ensure):
        """prompt 标示下一步「PM 出方案」。"""
        self.fn({})
        assert "PM 出方案" in self.mock.call_history[0][2]


class TestMasterFlushClarifyConv:
    """master_flush_clarify_conv — 配置验证。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_FLUSH_CLARIFY_DEF.nodes["master_flush_clarify_conv"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_ctx("phase_summary_path", "/tmp/summary.md")

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_phase_flushed_returned(self, mock_save, mock_open):
        """phase 应为 clarify_conv_flushed。"""
        result = self.fn({})
        assert result["phase"] == "clarify_conv_flushed"

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_checkpoint_resume_node(self, mock_save, mock_open):
        """save_checkpoint 使用 resume_node=pm_handoff。"""
        self.fn({})
        mock_save.assert_called_once_with(
            self.rt, "pm_handoff", "需求澄清",
            summary_path="/tmp/summary.md")

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_closes_master_conv(self, mock_save, mock_open):
        """关闭旧 master 对话。"""
        self.fn({})
        assert ("close:master", "master-test", "") in self.mock.call_history


class TestMasterFlushPMSummary:
    """master_flush_pm_summary — 配置验证。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_FLUSH_PM_DEF.nodes["master_flush_pm_summary"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_returns_pm_flushed_phase(self, mock_ensure):
        """phase 应为 pm_flushed。"""
        result = self.fn({})
        assert result["phase"] == "pm_flushed"

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_prompt_contains_artifacts(self, mock_ensure):
        """prompt 包含 PM 阶段的产出物路径。"""
        self.fn({})
        prompt = self.mock.call_history[0][2]
        assert "PRD.md" in prompt
        assert "prototype.html" in prompt
        assert "criteria-pm.md" in prompt


class TestMasterFlushPMConv:
    """master_flush_pm_conv — 配置验证。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_FLUSH_PM_DEF.nodes["master_flush_pm_conv"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_ctx("phase_summary_path", "/tmp/summary.md")

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_phase_flushed_returned(self, mock_save, mock_open):
        """phase 应为 pm_conv_flushed。"""
        result = self.fn({})
        assert result["phase"] == "pm_conv_flushed"

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_checkpoint_resume_node(self, mock_save, mock_open):
        """save_checkpoint 使用 resume_node=dev_handoff。"""
        self.fn({})
        mock_save.assert_called_once_with(
            self.rt, "dev_handoff", "PM 出方案",
            summary_path="/tmp/summary.md")

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_closes_master_conv(self, mock_save, mock_open):
        """关闭旧 master 对话。"""
        self.fn({})
        assert ("close:master", "master-test", "") in self.mock.call_history


class TestMasterFlushDevSummary:
    """master_flush_dev_summary — 配置验证。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_FLUSH_DEV_DEF.nodes["master_flush_dev_summary"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_returns_dev_flushed_phase(self, mock_ensure):
        """phase 应为 dev_flushed。"""
        result = self.fn({})
        assert result["phase"] == "dev_flushed"

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_prompt_contains_dev_artifacts(self, mock_ensure):
        """prompt 包含 Dev 阶段的产出物路径。"""
        self.fn({})
        prompt = self.mock.call_history[0][2]
        assert "design.md" in prompt
        assert "plan.md" in prompt


class TestMasterFlushDevConv:
    """master_flush_dev_conv — 配置验证。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_FLUSH_DEV_DEF.nodes["master_flush_dev_conv"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_ctx("phase_summary_path", "/tmp/summary.md")

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_phase_flushed_returned(self, mock_save, mock_open):
        """phase 应为 dev_conv_flushed。"""
        result = self.fn({})
        assert result["phase"] == "dev_conv_flushed"

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_checkpoint_resume_node(self, mock_save, mock_open):
        """save_checkpoint 使用 resume_node=qa_handoff。"""
        self.fn({})
        mock_save.assert_called_once_with(
            self.rt, "qa_handoff", "Dev 实现",
            summary_path="/tmp/summary.md")

    @patch("workflow.subgraphs.master_flush.open_master_conv", return_value="new-master-conv")
    @patch("workflow.subgraphs.master_flush.save_checkpoint")
    def test_closes_master_conv(self, mock_save, mock_open):
        """关闭旧 master 对话。"""
        self.fn({})
        assert ("close:master", "master-test", "") in self.mock.call_history
