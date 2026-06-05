"""Phase 1 节点测试：JudgeMasterReply / MasterReplyPM / ClarifyInject。"""
from __future__ import annotations
from unittest.mock import patch
import pytest
from agent_runtime import AgentRuntime
from workflow.phase1 import JudgeMasterReply, MasterReplyPM, ClarifyInject


class TestJudgeMasterReply:
    """judge_master_reply — Type B（路由），judge_reply 返回值决定分支。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=None, conversation_client=mock_client)
        JudgeMasterReply._runtime = self.rt

    @patch("workflow.phase1.judge_reply", return_value="A")
    def test_routes_A_when_judge_returns_A(self, mock_judge):
        """judge 返回 A 时 judge_result 应为 A，指向 pm_criteria。"""
        self.rt.context.set_ctx("master_reply", "master 回复内容")
        result = JudgeMasterReply.run({})
        assert result["judge_result"] == "A"

    @patch("workflow.phase1.judge_reply", return_value="B")
    def test_routes_B_when_judge_returns_B(self, mock_judge):
        """judge 返回 B 时 judge_result 应为 B，指向 pm_align。"""
        self.rt.context.set_ctx("master_reply", "master 回复内容")
        result = JudgeMasterReply.run({})
        assert result["judge_result"] == "B"

    @patch("workflow.phase1.judge_reply", return_value="C")
    def test_routes_C_when_judge_returns_C(self, mock_judge):
        """judge 返回 C 时 judge_result 应为 C，指向 clarify_inject。"""
        self.rt.context.set_ctx("master_reply", "master 回复内容")
        result = JudgeMasterReply.run({})
        assert result["judge_result"] == "C"

    @patch("workflow.phase1.judge_reply", return_value="A")
    def test_reads_master_reply_from_context(self, mock_judge):
        """judge_reply 收到的评读内容应来自 context 中的 master_reply。"""
        self.rt.context.set_ctx("master_reply", "具体回复内容")
        JudgeMasterReply.run({})
        call_args = mock_judge.call_args[0]
        assert call_args[2] == "具体回复内容"


class TestMasterReplyPM:
    """master_reply_pm — Type A（纯 call_agent），含文件/缓存降级路径。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=None, conversation_client=mock_client)
        MasterReplyPM._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    def test_raises_when_no_pm_reply(self):
        """pm_reply 既不在文件也不在 context 时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="PM 回信缺失"):
            MasterReplyPM.run({})

    def test_uses_cached_pm_reply_when_no_file(self, tmp_path):
        """没有 pm_reply_path 文件时，应从 context 中的 pm_reply_text 读取。"""
        self.rt.context.set_ctx("pm_reply_text", "缓存的 PM 回信")
        MasterReplyPM.run({})
        prompt = self.mock.call_history[0][2]
        assert "缓存的 PM 回信" in prompt

    def test_stores_master_reply_in_context(self):
        """Master 的回复应存入 context.master_reply。"""
        self.rt.context.set_ctx("pm_reply_text", "PM 回信")
        MasterReplyPM.run({})
        assert self.rt.context.get_ctx("master_reply") is not None

    def test_returns_master_reply_done_phase(self):
        """run 完成后 state.phase 应为 master_reply_done。"""
        self.rt.context.set_ctx("pm_reply_text", "PM 回信")
        result = MasterReplyPM.run({})
        assert result["phase"] == "master_reply_done"

    def test_task_prompt_contains_checklist(self):
        """prompt 中应包含检查清单关键词。"""
        self.rt.context.set_ctx("pm_reply_text", "PM 回信")
        MasterReplyPM.run({})
        prompt = self.mock.call_history[0][2]
        assert "逐一检查" in prompt
        assert "结论" in prompt


class TestClarifyInjectRecord:
    """clarify_inject_write — Type A（纯 call_agent）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=None, conversation_client=mock_client)
        ClarifyInject._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    def test_calls_master_agent(self):
        """record 应当调 Master 将澄清记录写入项目决策文件。"""
        ClarifyInject.record({})
        assert len(self.mock.call_history) == 1
        assert self.mock.call_history[0][0] == "master"

    def test_prompt_contains_project_context_path(self):
        """prompt 应包含 project_context.md 的路径。"""
        ClarifyInject.record({})
        prompt = self.mock.call_history[0][2]
        assert "project_context.md" in prompt

    def test_returns_clarify_done_phase(self):
        """record 完成后 state.phase 应为 clarify_done。"""
        result = ClarifyInject.record({})
        assert result["phase"] == "clarify_done"
