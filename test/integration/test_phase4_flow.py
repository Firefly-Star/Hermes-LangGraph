"""Phase 4 集成测试：一致性审计 → 维护文档 → 交付总结。"""
from __future__ import annotations
from unittest.mock import patch
import pytest
from agent_runtime import AgentRuntime
from workflow.phase4 import ConsistencyAudit, WriteMaintenanceDocs, DeliverySummary


class TestPhase4Flow:
    """Phase 4 线性段：context 传递和节点串联。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ConsistencyAudit._runtime = self.rt
        WriteMaintenanceDocs._runtime = self.rt
        DeliverySummary._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        # Set up some background context for artifact paths
        ws = self.rt.paths.workspace
        self.rt.context.set_ctx("pm_conv", "pm-conv")

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_context_propagation_through_three_nodes(self, mock_clear, mock_ensure):
        """ConsistencyAudit → WriteMaintenanceDocs → DeliverySummary 间 context 传递。"""
        state = {}

        # Node 1: ConsistencyAudit
        state = ConsistencyAudit.run(state)
        assert state["phase"] == "audit_done"
        audit_path = self.rt.context.get_ctx("audit_path")
        assert audit_path is not None
        assert "audit-report.md" in audit_path

        # Node 2: WriteMaintenanceDocs
        state = WriteMaintenanceDocs.run(state)
        assert state["phase"] == "docs_written"
        readme_path = self.rt.context.get_ctx("readme_path")
        deploy_path = self.rt.context.get_ctx("deploy_path")
        assert readme_path is not None
        assert deploy_path is not None

        # Node 3: DeliverySummary — 读取前两个节点写入的 context
        state = DeliverySummary.run(state)
        assert state["phase"] == "delivery_done"
        # 验证 audit/readme/deploy 路径出现在 prompt 中
        prompt = self.mock.call_history[2][2]
        assert audit_path in prompt or "审计报告" in prompt
        assert "README" in prompt
        assert "部署指南" in prompt

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_call_sequence_agent_assignment(self, mock_clear, mock_ensure):
        """三个节点各自调用正确的 agent。"""
        state = {}
        state = ConsistencyAudit.run(state)
        state = WriteMaintenanceDocs.run(state)
        state = DeliverySummary.run(state)

        assert len(self.mock.call_history) >= 3
        # ConsistencyAudit → master
        assert self.mock.call_history[0][0] == "master"
        # WriteMaintenanceDocs → dev
        assert self.mock.call_history[1][0] == "dev"
        # DeliverySummary → master
        assert self.mock.call_history[2][0] == "master"

    @patch("workflow.phase4.ensure_write_file", return_value=True)
    @patch("workflow.phase4.clear_checkpoint")
    def test_clear_checkpoint_called_after_audit(self, mock_clear, mock_ensure):
        """ConsistencyAudit 执行后应清除 checkpoint。"""
        ConsistencyAudit.run({})
        mock_clear.assert_called_once_with(self.rt)

    @patch("workflow.phase4.ensure_write_file", side_effect=[True, True, True])
    @patch("workflow.phase4.clear_checkpoint")
    def test_delivery_summary_uses_audit_path_when_present(self, mock_clear, mock_ensure):
        """DeliverySummary prompt 包含 audit_path（当 context 中有时）。"""
        self.rt.context.set_ctx("audit_path", "/tmp/test-audit.md")
        DeliverySummary.run({})
        prompt = self.mock.call_history[0][2]
        assert "/tmp/test-audit.md" in prompt
