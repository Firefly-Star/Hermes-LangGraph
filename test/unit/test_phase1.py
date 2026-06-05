"""Phase 1 节点测试：JudgeMasterReply / MasterReplyPM / ClarifyInject + 子图节点。"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import os
import pytest
from agent_runtime import AgentRuntime
from langgraph.graph import END
from workflow.phase1 import (JudgeMasterReply, MasterReplyPM, ClarifyInject,
                             PM_HANDOFF_DEF, PM_CRITERIA_DEF,
                             PMAlign, PMWriteDoc, ReviewPMOutput, HumanReview)


class TestJudgeMasterReply:
    """judge_master_reply — Type B（路由），judge_reply 返回值决定分支。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
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
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
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
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ClarifyInject._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "path/to/project_context.md")

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


# ── 子图节点 —— PM_HANDOFF_DEF ──

class TestPMHandoff:
    """pm_handoff — HandoffSubgraph 子图（Type A 纯 call_agent）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = PM_HANDOFF_DEF.nodes["pm_handoff"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写信调用的是 Master agent。"""
        self.fn({})
        assert len(self.mock.call_history) >= 1
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_uses_master_conv_from_context(self, mock_ensure):
        """写信 conversation 应来自 context 中的 master_conv。"""
        self.fn({})
        assert self.mock.call_history[0][1] == "master-test"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_stores_letter_path_in_context(self, mock_ensure):
        """pmletter_path 应存入 context。"""
        self.fn({})
        assert self.rt.context.get_ctx("pmletter_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_handoff_done_phase(self, mock_ensure):
        """完成后 state.phase 应为 pm_handoff_done。"""
        result = self.fn({})
        assert result["phase"] == "pm_handoff_done"

    def test_raises_when_no_master_conv(self):
        """master_conv 不存在时应抛出 RuntimeError。"""
        self.rt.context.set_ctx("master_conv", "")
        with pytest.raises(RuntimeError, match="master_conv 对话不存在"):
            self.fn({})


# ── 子图节点 —— PM_CRITERIA_DEF（pmwrite_criteria）──

class TestPMCriteriaWrite:
    """pmwrite_criteria — CriteriaDefinitionSubgraph 写标准节点（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = PM_CRITERIA_DEF.nodes["pmwrite_criteria"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写标准调用的是 Master agent。"""
        self.fn({})
        assert len(self.mock.call_history) >= 1
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_criteria_done_phase(self, mock_ensure):
        """完成后 state.phase 应为 pm_criteria_done。"""
        result = self.fn({})
        assert result["phase"] == "pm_criteria_done"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_judge_result_routes_to_review(self, mock_ensure):
        """judge_result 应为 review_pm_criteria（指向审查节点）。"""
        result = self.fn({})
        assert result["judge_result"] == "review_pm_criteria"


# ── 子图节点 —— PM_CRITERIA_DEF（review_pm_criteria）──

class TestPMCriteriaReview:
    """review_pm_criteria — CriteriaDefinitionSubgraph 审查节点（Type B 路由）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = PM_CRITERIA_DEF.nodes["review_pm_criteria"]
        self.fn._runtime = self.rt
        criteria_file = tmp_path / "criteria-pm.md"
        criteria_file.write_text("# 测试标准")
        self.rt.context.set_ctx("pm_criteria_path", str(criteria_file))

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="P")
    def test_routes_to_pass_when_judge_returns_P(self, mock_judge):
        """judge_reply 返回 P 时 judge_result 为 pm_write_doc。"""
        result = self.fn({})
        assert result["judge_result"] == "pm_write_doc"

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="F")
    def test_routes_to_fail_when_judge_returns_F(self, mock_judge):
        """judge_reply 返回 F 时 judge_result 为 pmwrite_criteria。"""
        result = self.fn({})
        assert result["judge_result"] == "pmwrite_criteria"

    def test_returns_early_when_no_criteria_file(self):
        """标准文件不存在时直接返回 fail，不调 agent。"""
        self.rt.context.set_ctx("pm_criteria_path", "/nonexistent/path")
        result = self.fn({})
        assert result["judge_result"] == "pmwrite_criteria"
        assert len(self.mock.call_history) == 0

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="P")
    def test_calls_reviewer_agent(self, mock_judge):
        """审查调用的是 reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="P")
    def test_prompt_contains_criteria_review_keywords(self, mock_judge):
        """prompt 应包含审查标准相关关键词。"""
        self.fn({})
        prompt = self.mock.call_history[0][2]
        assert "审查" in prompt


# ── 子图节点 —— PM_CRITERIA_DEF（review_to_pm_artifact）──

class TestPMCriteriaPassThrough:
    """review_to_pm_artifact — 直通节点，原样返回 state。"""

    def test_returns_state_unchanged(self):
        """state 不做任何修改直接返回。"""
        state = {"phase": "test", "data": "value"}
        fn = PM_CRITERIA_DEF.nodes["review_to_pm_artifact"]
        assert fn(state) == state


# ── 子图节点 —— PM_CRITERIA_DEF（review_pm_criteria_feedback）──

class TestPMCriteriaFeedback:
    """review_pm_criteria_feedback — 反馈节点（Type A 纯 call_agent）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = PM_CRITERIA_DEF.nodes["review_pm_criteria_feedback"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("pm_criteria_review", "审查意见内容")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_reviewer_agent(self, mock_ensure):
        """写反馈信调用的是 reviewer agent。"""
        self.fn({})
        assert len(self.mock.call_history) >= 1
        assert self.mock.call_history[0][0] == "reviewer"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_fail_judge_result(self, mock_ensure):
        """完成后 judge_result 为 pmwrite_criteria（回写标准节点）。"""
        result = self.fn({})
        assert result["judge_result"] == "pmwrite_criteria"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_clears_review_text_from_context(self, mock_ensure):
        """审查意见写入反馈信后应从 context 清空。"""
        self.fn({})
        assert self.rt.context.get_ctx("pm_criteria_review") == ""

    def test_raises_when_no_review_text(self):
        """审查意见为空时应抛出 RuntimeError。"""
        self.rt.context.set_ctx("pm_criteria_review", "")
        with pytest.raises(RuntimeError, match="审查意见为空"):
            self.fn({})


# ── ClarifyInject.interact ──

class TestClarifyInjectInteract:
    """clarify_inject — 用户确认循环（Type A，mocked clarify_loop）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ClarifyInject._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.phase1.clarify_loop", return_value="用户已确认")
    def test_stores_clarify_reason(self, mock_loop):
        """interact 应将 clarify_loop 的结束原因存入 context。"""
        ClarifyInject.interact({})
        assert self.rt.context.get_ctx("clarify_reason") == "用户已确认"

    @patch("workflow.phase1.clarify_loop", return_value="用户已确认")
    def test_returns_clarify_inject_write_phase(self, mock_loop):
        """interact 完成后 state.phase 应为 clarify_inject_write。"""
        result = ClarifyInject.interact({})
        assert result["phase"] == "clarify_inject_write"

    @patch("workflow.phase1.clarify_loop", return_value="")
    def test_handles_empty_reason(self, mock_loop):
        """clarify_loop 返回空字符串时也应正确存储。"""
        ClarifyInject.interact({})
        assert self.rt.context.get_ctx("clarify_reason") == ""


# ── PMAlign.master_reply ──

class TestPMAlignMasterReply:
    """pm_align_master_reply — Master 写信给 PM（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PMAlign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写信调用的是 Master agent。"""
        PMAlign.master_reply({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_stores_letter_path_in_context(self, mock_ensure):
        """masterletter_path 应存入 context。"""
        PMAlign.master_reply({})
        assert self.rt.context.get_ctx("masterletter_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_pm_align_read_phase(self, mock_ensure):
        """完成后 state.phase 应为 pm_align_read。"""
        result = PMAlign.master_reply({})
        assert result["phase"] == "pm_align_read"

    def test_raises_when_no_master_conv(self):
        """master_conv 不存在时应抛出 RuntimeError。"""
        self.rt.context.set_ctx("master_conv", "")
        with pytest.raises(RuntimeError, match="clarify conversation 不存在"):
            PMAlign.master_reply({})


# ── PMAlign.read ──

class TestPMAlignRead:
    """pm_align_read — PM 读信回信，含两轮不同路径（Type C）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PMAlign._runtime = self.rt

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_first_round_reads_pmletter(self, mock_ensure, tmp_path):
        """第一轮应读取 handoff 信件（pmletter_path），调 PM agent。"""
        letter = tmp_path / "pmletter.md"
        letter.write_text("Master 给 PM 的信")
        self.rt.context.set_ctx("pmletter_path", str(letter))
        PMAlign.read({})
        assert self.mock.call_history[0][0] == "pm"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_first_round_sets_pm_conv(self, mock_ensure, tmp_path):
        """第一轮应创建 pm_conv 并存入 context。"""
        letter = tmp_path / "pmletter.md"
        letter.write_text("内容")
        self.rt.context.set_ctx("pmletter_path", str(letter))
        PMAlign.read({})
        assert self.rt.context.get_ctx("pm_conv") is not None
        assert "pm-align" in self.rt.context.get_ctx("pm_conv")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_second_round_reads_masterletter(self, mock_ensure, tmp_path):
        """第二轮及之后应读取 masterletter_path。"""
        self.rt.context.set_ctx("pm_align_round", "1")
        letter = tmp_path / "masterletter.md"
        letter.write_text("Master 回信")
        self.rt.context.set_ctx("masterletter_path", str(letter))
        self.rt.context.set_ctx("pm_conv", "pm-align-test")
        PMAlign.read({})
        assert self.mock.call_history[0][0] == "pm"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_increments_round_number(self, mock_ensure, tmp_path):
        """每次调用后 pm_align_round +1。"""
        letter = tmp_path / "pmletter.md"
        letter.write_text("内容")
        self.rt.context.set_ctx("pmletter_path", str(letter))
        PMAlign.read({})
        assert self.rt.context.get_ctx("pm_align_round") == "1"

    def test_raises_when_no_letter_path(self):
        """pmletter_path 不存在时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="没有 handoff 信件路径"):
            PMAlign.read({})

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_stores_pm_reply_in_context(self, mock_ensure, tmp_path):
        """PM 回信内容应存入 context（pm_reply_text/pm_reply_path）。"""
        letter = tmp_path / "pmletter.md"
        letter.write_text("内容")
        self.rt.context.set_ctx("pmletter_path", str(letter))
        PMAlign.read({})
        assert self.rt.context.get_ctx("pm_reply_path") is not None
        # pm_reply_text 只有文件存在时才设，mock 不写文件故为空
        assert self.rt.context.get_ctx("pm_reply_text") == ""


# ── PMWriteDoc ──

class TestPMWriteDocWritePRD:
    """pm_write_prd_letter — Master 写信要求 PM 写 PRD（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PMWriteDoc._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写信调用的是 Master agent。"""
        PMWriteDoc.write_prd_letter({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_prd_paths_in_context(self, mock_ensure):
        """prd_path 和 prdletter_path 应存入 context。"""
        PMWriteDoc.write_prd_letter({})
        assert self.rt.context.get_ctx("prd_path") is not None
        assert self.rt.context.get_ctx("prdletter_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_pm_read_prd_phase(self, mock_ensure):
        """完成后 state.phase 应为 pm_read_prd。"""
        result = PMWriteDoc.write_prd_letter({})
        assert result["phase"] == "pm_read_prd"

    def test_raises_when_no_master_conv(self):
        """master_conv 不存在时应抛出 RuntimeError。"""
        self.rt.context.set_ctx("master_conv", "")
        with pytest.raises(RuntimeError, match="clarify conversation 不存在"):
            PMWriteDoc.write_prd_letter({})


class TestPMWriteDocReadPRD:
    """pm_read_prd_letter — PM 读信写 PRD.md（Type C）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PMWriteDoc._runtime = self.rt
        self.rt.context.set_ctx("pm_conv", "pm-test")
        letter = tmp_path / "prd-letter.md"
        letter.write_text("PRD 编写说明")
        self.rt.context.set_ctx("prdletter_path", str(letter))
        self.rt.context.set_ctx("prd_path", str(tmp_path / "PRD.md"))

    def test_calls_pm_agent(self):
        """读信调用的是 PM agent。"""
        PMWriteDoc.read_prd_letter({})
        assert self.mock.call_history[0][0] == "pm"

    def test_returns_pm_write_proto_phase(self):
        """完成后 state.phase 应为 pm_write_proto_letter。"""
        result = PMWriteDoc.read_prd_letter({})
        assert result["phase"] == "pm_write_proto_letter"

    def test_prompt_contains_prd_path(self):
        """prompt 应包含 PRD.md 路径。"""
        PMWriteDoc.read_prd_letter({})
        prompt = self.mock.call_history[0][2]
        assert "PRD.md" in prompt

    def test_deletes_letter_after_read(self):
        """读完信后应删除信件文件。"""
        letter_path = self.rt.context.get_ctx("prdletter_path")
        PMWriteDoc.read_prd_letter({})
        assert not os.path.exists(letter_path)


class TestPMWriteDocWriteProto:
    """pm_write_proto_letter — Master 写信要求 PM 写 prototype（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PMWriteDoc._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_ctx("pm_dir", str(tmp_path / "PM"))

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写信调用的是 Master agent。"""
        PMWriteDoc.write_proto_letter({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_proto_paths_in_context(self, mock_ensure):
        """proto_path 和 protoletter_path 应存入 context。"""
        PMWriteDoc.write_proto_letter({})
        assert self.rt.context.get_ctx("proto_path") is not None
        assert self.rt.context.get_ctx("protoletter_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_pm_read_proto_phase(self, mock_ensure):
        """完成后 state.phase 应为 pm_read_proto。"""
        result = PMWriteDoc.write_proto_letter({})
        assert result["phase"] == "pm_read_proto"


class TestPMWriteDocReadProto:
    """pm_read_proto_letter — PM 读信写 prototype.html（Type C）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PMWriteDoc._runtime = self.rt
        self.rt.context.set_ctx("pm_conv", "pm-test")
        self.rt.context.set_ctx("pm_dir", str(tmp_path / "PM"))
        letter = tmp_path / "proto-letter.md"
        letter.write_text("原型编写说明")
        self.rt.context.set_ctx("protoletter_path", str(letter))
        self.rt.context.set_ctx("proto_path", str(tmp_path / "prototype.html"))

    def test_calls_pm_agent(self):
        """读信调用的是 PM agent。"""
        PMWriteDoc.read_proto_letter({})
        assert self.mock.call_history[0][0] == "pm"

    def test_returns_done_phase_with_pass_judge(self):
        """完成后 state.phase 应为 done，judge_result 应为 pass。"""
        result = PMWriteDoc.read_proto_letter({})
        assert result["phase"] == "done"
        assert result["judge_result"] == "pass"

    def test_deletes_letter_after_read(self):
        """读完信后应删除信件文件。"""
        letter_path = self.rt.context.get_ctx("protoletter_path")
        PMWriteDoc.read_proto_letter({})
        assert not os.path.exists(letter_path)


# ── ReviewPMOutput ──

class TestReviewPMOutput:
    """review_pm_output — Reviewer 审查 PM 产出（Type B 路由）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ReviewPMOutput._runtime = self.rt

    @patch("workflow.phase1.judge_reply", return_value="P")
    def test_routes_to_human_review_when_P(self, mock_judge):
        """judge_reply 返回 P 时 judge_result 应为 human_review。"""
        result = ReviewPMOutput.run({})
        assert result["judge_result"] == "human_review"

    @patch("workflow.phase1.judge_reply", return_value="F")
    def test_routes_to_pm_write_doc_when_F(self, mock_judge):
        """judge_reply 返回 F 时 judge_result 应为 pm_write_doc。"""
        result = ReviewPMOutput.run({})
        assert result["judge_result"] == "pm_write_doc"

    @patch("workflow.phase1.judge_reply", return_value="P")
    def test_calls_reviewer_agent(self, mock_judge):
        """审查调用的是 reviewer agent。"""
        ReviewPMOutput.run({})
        assert self.mock.call_history[0][0] == "reviewer"

    @patch("workflow.phase1.judge_reply", return_value="P")
    def test_stores_review_result_in_context(self, mock_judge):
        """审查结果应存入 context.review_result。"""
        ReviewPMOutput.run({})
        assert self.rt.context.get_ctx("review_result") is not None

    @patch("workflow.phase1.judge_reply", return_value="F")
    def test_returns_review_done_phase(self, mock_judge):
        """完成后 state.phase 应为 review_done。"""
        result = ReviewPMOutput.run({})
        assert result["phase"] == "review_done"


# ── HumanReview ──

class TestHumanReview:
    """human_review — 人工审核 PM 产出（Type D，mocked checkpoint.wait）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        HumanReview._runtime = self.rt

    def test_passes_when_empty_feedback(self):
        """空输入（EOF）表示通过，judge_result 应为 END。"""
        self.rt.checkpoint.wait = MagicMock(return_value=MagicMock(message=""))
        result = HumanReview.run({})
        assert result["judge_result"] == END

    def test_passes_phase_is_done_when_empty(self):
        """空输入时 state.phase 应为 done。"""
        self.rt.checkpoint.wait = MagicMock(return_value=MagicMock(message=""))
        result = HumanReview.run({})
        assert result["phase"] == "done"

    def test_rejects_when_feedback_provided(self):
        """有反馈时 judge_result 应为 pm_write_doc（回 PM 修改）。"""
        self.rt.checkpoint.wait = MagicMock(return_value=MagicMock(message="需要修改"))
        result = HumanReview.run({})
        assert result["judge_result"] == "pm_write_doc"

    def test_rejected_phase_is_human_review_rejected(self):
        """有反馈时 state.phase 应为 human_review_rejected。"""
        self.rt.checkpoint.wait = MagicMock(return_value=MagicMock(message="需要修改"))
        result = HumanReview.run({})
        assert result["phase"] == "human_review_rejected"

    def test_stores_feedback_in_context(self):
        """反馈内容应追加到 context.human_feedback。"""
        self.rt.checkpoint.wait = MagicMock(return_value=MagicMock(message="UI 需要优化"))
        HumanReview.run({})
        feedback = self.rt.context.get_ctx("human_feedback")
        assert feedback is not None
        assert "UI 需要优化" in feedback

    def test_increments_feedback_round(self):
        """每次有反馈时 human_feedback_round +1。"""
        self.rt.checkpoint.wait = MagicMock(return_value=MagicMock(message="修改 A"))
        HumanReview.run({})
        assert self.rt.context.get_ctx("human_feedback_round") == 1

    def test_accumulates_multiple_feedback_rounds(self):
        """多轮反馈应累积，不覆盖之前的内容。"""
        self.rt.context.set_ctx("human_feedback", "第 1 轮反馈")
        self.rt.checkpoint.wait = MagicMock(return_value=MagicMock(message="第 2 轮反馈"))
        HumanReview.run({})
        feedback = self.rt.context.get_ctx("human_feedback")
        assert "第 1 轮反馈" in feedback
        assert "第 2 轮反馈" in feedback
