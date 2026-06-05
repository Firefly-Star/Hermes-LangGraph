"""DeliverySummary 节点测试：验证 call_agent 调用 + 状态转换。"""
from __future__ import annotations
import pytest
from agent_runtime import AgentRuntime
from workflow.phase4 import DeliverySummary


class TestDeliverySummary:
    """DeliverySummary.run() 使用 MockClient 的单元测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_client):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=None, conversation_client=mock_client)
        DeliverySummary._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test-1")
        self.rt.context.set_ctx("audit_path", "/tmp/audit-report.md")
        self.rt.context.set_ctx("readme_path", "/tmp/README.md")
        self.rt.context.set_ctx("deploy_path", "/tmp/deploy.md")

    def test_returns_delivery_done_state(self):
        result = DeliverySummary.run({})
        assert result["phase"] == "delivery_done"
        assert result["judge_result"] == ""

    def test_uses_master_conv(self):
        DeliverySummary.run({})
        assert len(self.mock.call_history) >= 1
        agent, conv, _ = self.mock.call_history[0]
        assert agent == "master"
        assert conv == "master-test-1"

    def test_prompt_contains_summary_path(self):
        DeliverySummary.run({})
        agent, _, prompt = self.mock.call_history[0]
        assert "delivery-summary.md" in prompt
        assert "交付" in prompt

    def test_calls_ensure_write_file(self):
        DeliverySummary.run({})
        # ensure_write_file 会额外多次 call_agent（文件不存在时的 retry 提醒）
        master_calls = [(a, c) for a, c, _ in self.mock.call_history if a == "master"]
        assert len(master_calls) >= 2  # 至少 1 次正文 + 1 次提醒

    def test_sets_delivery_summary_path_in_context(self):
        DeliverySummary.run({})
        path = self.rt.context.get_ctx("delivery_summary_path")
        assert path is not None
        assert "delivery-summary.md" in str(path)
