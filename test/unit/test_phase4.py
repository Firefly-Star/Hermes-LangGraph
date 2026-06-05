"""Phase 4 节点测试：ConsistencyAudit / WriteMaintenanceDocs / DeliverySummary。"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import os
import pytest
from agent_runtime import AgentRuntime
from workflow.phase4 import ConsistencyAudit, WriteMaintenanceDocs, DeliverySummary


class TestConsistencyAudit:
    """consistency_audit — Master 四方一致性审计（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ConsistencyAudit._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_calls_master_agent(self, mock_clear, mock_ensure):
        """审计调 Master agent。"""
        ConsistencyAudit.run({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_uses_master_conv(self, mock_clear, mock_ensure):
        """使用 master_conv 对话。"""
        ConsistencyAudit.run({})
        assert self.mock.call_history[0][1] == "master-test"

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_prompt_contains_audit_keywords(self, mock_clear, mock_ensure):
        """prompt 包含审计与四方一致性关键词。"""
        ConsistencyAudit.run({})
        prompt = self.mock.call_history[0][2]
        assert "一致性审计" in prompt
        assert "四方" in prompt

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_clears_checkpoint(self, mock_clear, mock_ensure):
        """审计完成后清除 checkpoint。"""
        ConsistencyAudit.run({})
        mock_clear.assert_called_once_with(self.rt)

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_stores_audit_path(self, mock_clear, mock_ensure):
        """audit_path 应存入 context。"""
        ConsistencyAudit.run({})
        path = self.rt.context.get_ctx("audit_path")
        assert path is not None
        assert "audit-report.md" in path

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_returns_audit_done_phase(self, mock_clear, mock_ensure):
        """phase 应为 audit_done。"""
        result = ConsistencyAudit.run({})
        assert result["phase"] == "audit_done"

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_includes_project_context_when_set(self, mock_clear, mock_ensure):
        """project_context_path 存在时 artifacts 含决策记录路径。"""
        self.rt.context.set_bg("project_context_path", "path/to/context.md")
        ConsistencyAudit.run({})
        prompt = self.mock.call_history[0][2]
        assert "项目决策记录" in prompt

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_calls_ensure_write_file(self, mock_clear, mock_ensure):
        """审计报告调用 ensure_write_file。"""
        ConsistencyAudit.run({})
        assert mock_ensure.called


class TestWriteMaintenanceDocs:
    """write_maintenance_docs — Dev 写维护文档（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        WriteMaintenanceDocs._runtime = self.rt

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_calls_dev_agent(self, mock_ensure):
        """写文档调 Dev agent。"""
        WriteMaintenanceDocs.run({})
        assert self.mock.call_history[0][0] == "dev"

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_uses_dev_doc_conv(self, mock_ensure):
        """使用 dev-doc 对话（前缀匹配即可）。"""
        WriteMaintenanceDocs.run({})
        assert "dev-doc" in self.mock.call_history[0][1]

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_prompt_contains_readme_and_deploy(self, mock_ensure):
        """prompt 包含 README.md 和 deployment-guide.md。"""
        WriteMaintenanceDocs.run({})
        prompt = self.mock.call_history[0][2]
        assert "README.md" in prompt
        assert "deployment-guide.md" in prompt

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_stores_paths_in_context(self, mock_ensure):
        """readme_path 和 deploy_path 应存入 context。"""
        WriteMaintenanceDocs.run({})
        assert self.rt.context.get_ctx("readme_path") is not None
        assert self.rt.context.get_ctx("deploy_path") is not None

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_returns_docs_written_phase(self, mock_ensure):
        """phase 应为 docs_written。"""
        result = WriteMaintenanceDocs.run({})
        assert result["phase"] == "docs_written"

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_calls_ensure_write_file_twice(self, mock_ensure):
        """ensure_write_file 应被调用至少两次（README + deploy-guide）。"""
        WriteMaintenanceDocs.run({})
        assert mock_ensure.call_count >= 2

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_judge_result_empty(self, mock_ensure):
        """judge_result 应为空字符串。"""
        result = WriteMaintenanceDocs.run({})
        assert result["judge_result"] == ""


class TestDeliverySummary:
    """delivery_summary — Master 写交付总结（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DeliverySummary._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """交付总结调 Master agent。"""
        DeliverySummary.run({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_uses_master_conv(self, mock_ensure):
        """使用 master_conv 对话。"""
        DeliverySummary.run({})
        assert self.mock.call_history[0][1] == "master-test"

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_prompt_contains_summary_keywords(self, mock_ensure):
        """prompt 包含交付总结和产出物清单关键词。"""
        DeliverySummary.run({})
        prompt = self.mock.call_history[0][2]
        assert "交付总结" in prompt
        assert "产出物" in prompt

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_stores_summary_path(self, mock_ensure):
        """delivery_summary_path 应存入 context。"""
        DeliverySummary.run({})
        assert self.rt.context.get_ctx("delivery_summary_path") is not None

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_returns_delivery_done_phase(self, mock_ensure):
        """phase 应为 delivery_done。"""
        result = DeliverySummary.run({})
        assert result["phase"] == "delivery_done"

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_judge_result_empty(self, mock_ensure):
        """judge_result 应为空字符串。"""
        result = DeliverySummary.run({})
        assert result["judge_result"] == ""

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_artifacts_include_context_paths_when_set(self, mock_ensure):
        """context 中有 audit/readme/deploy 路径时 artifacts 包含对应项。"""
        self.rt.context.set_ctx("audit_path", "/tmp/audit.md")
        self.rt.context.set_ctx("readme_path", "/tmp/README.md")
        self.rt.context.set_ctx("deploy_path", "/tmp/deploy.md")
        DeliverySummary.run({})
        prompt = self.mock.call_history[0][2]
        assert "审计报告" in prompt
        assert "README" in prompt
        assert "部署指南" in prompt

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_artifacts_omit_context_paths_when_not_set(self, mock_ensure):
        """context 中无 audit/readme/deploy 路径时 artifacts 不含对应项。"""
        # 不设任何路径，保证基础产出物仍列出
        DeliverySummary.run({})
        prompt = self.mock.call_history[0][2]
        assert "审计报告" not in prompt
        assert "README" not in prompt
        assert "部署指南" not in prompt
        # 基础产出物仍然存在
        assert "PRD" in prompt
        assert "测试报告" in prompt
