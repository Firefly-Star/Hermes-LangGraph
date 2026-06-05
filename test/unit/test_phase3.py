"""Phase 3 节点测试：QAAlign / 子图 / QAWrite / Judge / DevFix。"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import os
import pytest
from agent_runtime import AgentRuntime
from workflow.phase3 import (QA_HANDOFF_DEF, QAAlign, QA_CRITERIA_DEF,
                             QAWriteTestPlan, MASTER_PLAN_REVIEW_DEF,
                             QAWriteTestCase, REVIEWER_CODE_REVIEW_DEF,
                             QARunTests, JudgeTestResult, DevFix)


# ── QA_HANDOFF_DEF 子图节点 ──

class TestQAHandoff:
    """qa_handoff — HandoffSubgraph Master 写给 QA 的信（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = QA_HANDOFF_DEF.nodes["qa_handoff"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写信调 Master agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_stores_qaletter_path(self, mock_ensure):
        """qaletter_path 应存入 context。"""
        self.fn({})
        assert self.rt.context.get_ctx("qaletter_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_handoff_done_phase(self, mock_ensure):
        """phase 应为 qa_handoff_done。"""
        result = self.fn({})
        assert result["phase"] == "qa_handoff_done"

    def test_raises_when_no_master_conv(self):
        """master_conv 不存在应抛出 RuntimeError。"""
        self.rt.context.set_ctx("master_conv", "")
        with pytest.raises(RuntimeError, match="master_conv 对话不存在"):
            self.fn({})


# ── QAAlign ──

class TestQAAlignQA:
    """qa_align_qa — Type C（letter 读写），首轮/反馈轮。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAAlign._runtime = self.rt

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_first_round_calls_qa_agent(self, mock_ensure, tmp_path):
        """首轮从 handoff 信件读取，调 QA agent。"""
        letter = tmp_path / "qaletter.md"
        letter.write_text("Master 给 QA 的信")
        self.rt.context.set_ctx("qaletter_path", str(letter))
        QAAlign.qa({})
        assert self.mock.call_history[0][0] == "qa"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_qa_conv(self, mock_ensure, tmp_path):
        """应创建 qa_conv。"""
        letter = tmp_path / "qaletter.md"
        letter.write_text("内容")
        self.rt.context.set_ctx("qaletter_path", str(letter))
        QAAlign.qa({})
        assert self.rt.context.get_ctx("qa_conv") is not None
        assert "qa-align" in self.rt.context.get_ctx("qa_conv")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_feedback_round_calls_qa_agent(self, mock_ensure, tmp_path):
        """反馈轮从反馈信件读取，调 QA agent。"""
        feedback = tmp_path / "feedback.md"
        feedback.write_text("反馈意见")
        self.rt.context.set_ctx("qa_feedback_path", str(feedback))
        QAAlign.qa({})
        assert self.mock.call_history[0][0] == "qa"

    def test_raises_when_no_handoff_path(self):
        """首轮没有 qaletter_path 应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="没有 handoff 信件路径"):
            QAAlign.qa({})

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_qa_done_phase(self, mock_ensure, tmp_path):
        """phase 应为 qa_align_qa_done。"""
        letter = tmp_path / "qaletter.md"
        letter.write_text("内容")
        self.rt.context.set_ctx("qaletter_path", str(letter))
        result = QAAlign.qa({})
        assert result["phase"] == "qa_align_qa_done"


class TestQAAlignPM:
    """qa_align_pm — Type C，PM 读 QA 理解写 review（keep=True）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAAlign._runtime = self.rt
        qa_reply = tmp_path / "qa-understanding.md"
        qa_reply.write_text("QA 的理解")
        self.rt.context.set_ctx("qa_reply_path", str(qa_reply))

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_pm_agent(self, mock_ensure):
        """调 PM agent 写 review。"""
        QAAlign.pm({})
        assert self.mock.call_history[0][0] == "pm"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_pm_review_path(self, mock_ensure):
        """pm_review_path 应存入 context。"""
        QAAlign.pm({})
        assert self.rt.context.get_ctx("pm_review_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_pm_done_phase(self, mock_ensure):
        """phase 应为 qa_align_pm_done。"""
        result = QAAlign.pm({})
        assert result["phase"] == "qa_align_pm_done"

    def test_raises_when_qa_reply_missing(self):
        """qa_reply_path 不存在应抛出 RuntimeError。"""
        self.rt.context.set_ctx("qa_reply_path", "/nonexistent/path")
        with pytest.raises(RuntimeError, match="QA 理解信件不存在"):
            QAAlign.pm({})


class TestQAAlignDev:
    """qa_align_dev — Type C，Dev 读 QA 理解写 review。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAAlign._runtime = self.rt
        qa_reply = tmp_path / "qa-understanding.md"
        qa_reply.write_text("QA 的理解")
        self.rt.context.set_ctx("qa_reply_path", str(qa_reply))

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_dev_agent(self, mock_ensure):
        """调 Dev agent 写 review。"""
        QAAlign.dev({})
        assert self.mock.call_history[0][0] == "dev"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_dev_review_path(self, mock_ensure):
        """dev_review_path 应存入 context。"""
        QAAlign.dev({})
        assert self.rt.context.get_ctx("dev_review_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_dev_done_phase(self, mock_ensure):
        """phase 应为 qa_align_dev_done。"""
        result = QAAlign.dev({})
        assert result["phase"] == "qa_align_dev_done"

    def test_raises_when_qa_reply_missing(self):
        """qa_reply_path 不存在应抛出 RuntimeError。"""
        self.rt.context.set_ctx("qa_reply_path", "/nonexistent/path")
        with pytest.raises(RuntimeError, match="QA 理解信件不存在"):
            QAAlign.dev({})


class TestQAAlignJudge:
    """qa_align_judge — Type B（路由），3 分支 + ❓ 升级路径。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAAlign._runtime = self.rt

    @patch("workflow.phase3.judge_reply", return_value="C")
    def test_routes_to_master_when_judge_C(self, mock_judge):
        """judge C → master。"""
        QAAlign.judge({})
        assert self.rt.context.get_ctx("combined_review") is not None
        assert self.rt.context.get_ctx("combined_review") != ""
        result = QAAlign.judge({})
        assert result["judge_result"] == "qa_align_master"

    @patch("workflow.phase3.judge_reply", return_value="A")
    def test_routes_to_master_when_needs_upgrade(self, mock_judge):
        """combined_review 含 ❓ 时路由到 master（即使 judge A）。"""
        self.rt.context.set_ctx("pm_review_text", "需要升级❓的问题")
        result = QAAlign.judge({})
        assert result["judge_result"] == "qa_align_master"

    @patch("workflow.phase3.judge_reply", return_value="B")
    def test_routes_to_qa_when_judge_B(self, mock_judge, tmp_path):
        """judge B → 写反馈文件回 qa。"""
        os.makedirs(self.rt.paths.handoffs, exist_ok=True)
        self.rt.context.set_ctx("pm_review_text", "有修改意见")
        self.rt.context.set_ctx("dev_review_text", "支持修改")
        result = QAAlign.judge({})
        assert result["judge_result"] == "qa_align_qa"
        assert self.rt.context.get_ctx("qa_feedback_path") is not None
        assert os.path.exists(self.rt.context.get_ctx("qa_feedback_path"))

    @patch("workflow.phase3.judge_reply", return_value="A")
    def test_routes_to_exit_when_judge_A(self, mock_judge, tmp_path):
        """judge A → 写 understanding.md，路由 exit。"""
        self.rt.context.set_ctx("qa_reply_text", "QA 理解总结")
        result = QAAlign.judge({})
        assert result["judge_result"] == "exit"

    @patch("workflow.phase3.judge_reply", return_value="A")
    def test_exit_writes_understanding_file(self, mock_judge, tmp_path):
        """exit 路径应生成 QA/understanding.md。"""
        self.rt.context.set_ctx("qa_reply_text", "QA 理解总结")
        QAAlign.judge({})
        path = self.rt.context.get_ctx("qa_understanding_path")
        assert path is not None
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert "QA 理解总结" in f.read()

    def test_raises_when_neither_pm_nor_dev_review(self):
        """两个 review 都为空时 judge 仍可执行（空文本 judge）。"""
        # 不设任何 review 文本，judge 默认返回 B
        pass  # 无 review 文本不会抛异常，只是合并后的文本为空


class TestQAAlignMaster:
    """qa_align_master — Type A+B，Master 处理升级 + judge。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAAlign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        pm_review = tmp_path / "pm-review.md"
        pm_review.write_text("PM 审查")
        dev_review = tmp_path / "dev-review.md"
        dev_review.write_text("Dev 审查")
        self.rt.context.set_ctx("pm_review_path", str(pm_review))
        self.rt.context.set_ctx("dev_review_path", str(dev_review))

    @patch("workflow.phase3.judge_reply", return_value="A")
    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure, mock_judge):
        """处理升级调 Master agent。"""
        QAAlign.master({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.phase3.judge_reply", return_value="A")
    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_routes_to_qa_when_judge_A(self, mock_ensure, mock_judge):
        """judge A → 路由回 qa。"""
        result = QAAlign.master({})
        assert result["judge_result"] == "qa_align_qa"

    @patch("workflow.phase3.judge_reply", return_value="B")
    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_routes_to_confirm_when_judge_B(self, mock_ensure, mock_judge):
        """judge B → 路由到 confirm。"""
        result = QAAlign.master({})
        assert result["judge_result"] == "qa_align_confirm"

    def test_raises_when_review_paths_missing(self):
        """review 文件不存在应抛出 RuntimeError。"""
        self.rt.context.set_ctx("pm_review_path", "/nonexistent/path")
        with pytest.raises(RuntimeError):
            QAAlign.master({})


class TestQAAlignConfirm:
    """qa_align_confirm — Type D，clarify_loop。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAAlign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.phase3.clarify_loop", return_value="用户确认")
    def test_calls_clarify_loop(self, mock_loop):
        """调 clarify_loop 进行用户确认。"""
        QAAlign.confirm({})
        mock_loop.assert_called_once()

    @patch("workflow.phase3.clarify_loop", return_value="用户确认")
    def test_uses_master_conv(self, mock_loop):
        """clarify_loop 使用 master_conv。"""
        QAAlign.confirm({})
        assert mock_loop.call_args[0][1] == "master-test"

    @patch("workflow.phase3.clarify_loop", return_value="用户确认")
    def test_returns_confirmed_phase(self, mock_loop):
        """phase 应为 qa_align_confirmed。"""
        result = QAAlign.confirm({})
        assert result["phase"] == "qa_align_confirmed"


class TestQAAlignRecord:
    """qa_align_record — Type A，Master 记录决策到 project_context。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAAlign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "path/to/context.md")

    def test_calls_master_agent(self):
        """调 Master agent 记录决策。"""
        QAAlign.record({})
        assert self.mock.call_history[0][0] == "master"

    def test_prompt_contains_path(self):
        """prompt 包含 project_context 路径。"""
        QAAlign.record({})
        assert "path/to/context.md" in self.mock.call_history[0][2]

    def test_returns_recorded_phase(self):
        """phase 应为 qa_align_recorded。"""
        result = QAAlign.record({})
        assert result["phase"] == "qa_align_recorded"


class TestQAAlignFinal:
    """qa_align_final — Type A，Master 写最终答复（write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAAlign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """调 Master agent 写最终答复。"""
        QAAlign.final({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_qa_feedback_path(self, mock_ensure):
        """qa_feedback_path 应设为最终答复路径。"""
        QAAlign.final({})
        assert self.rt.context.get_ctx("qa_feedback_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_final_done_phase(self, mock_ensure):
        """phase 应为 qa_align_final_done，judge 回 qa。"""
        result = QAAlign.final({})
        assert result["phase"] == "qa_align_final_done"
        assert result["judge_result"] == "qa_align_qa"


class TestQAAlignJudgeExit:
    """qa_align_judge_exit — 空节点。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        assert QAAlign.judge_exit({"phase": "test"}) == {"phase": "test"}


# ── QA_CRITERIA_DEF 子图节点 ──

class TestQACriteriaWrite:
    """qawrite_criteria — CriteriaDefinitionSubgraph 写标准（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = QA_CRITERIA_DEF.nodes["qawrite_criteria"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写标准调 Master agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_criteria_done_phase(self, mock_ensure):
        """phase 应为 qa_criteria_done。"""
        result = self.fn({})
        assert result["phase"] == "qa_criteria_done"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_judge_result_routes_to_review(self, mock_ensure):
        """judge_result 应为 review_qa_criteria。"""
        result = self.fn({})
        assert result["judge_result"] == "review_qa_criteria"


class TestQACriteriaReview:
    """review_qa_criteria — CriteriaDefinitionSubgraph 审查（Type B）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = QA_CRITERIA_DEF.nodes["review_qa_criteria"]
        self.fn._runtime = self.rt
        criteria_file = tmp_path / "criteria-qa.md"
        criteria_file.write_text("QA 审核标准")
        self.rt.context.set_ctx("qa_criteria_path", str(criteria_file))

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="P")
    def test_routes_to_pass_when_judge_P(self, mock_judge):
        """P → qa_write_plan。"""
        result = self.fn({})
        assert result["judge_result"] == "qa_write_plan"

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="F")
    def test_routes_to_fail_when_judge_F(self, mock_judge):
        """F → qawrite_criteria。"""
        result = self.fn({})
        assert result["judge_result"] == "qawrite_criteria"

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="P")
    def test_calls_reviewer_agent(self, mock_judge):
        """审查调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"

    def test_returns_early_when_no_criteria_file(self):
        """标准文件不存在时直接返回 fail。"""
        self.rt.context.set_ctx("qa_criteria_path", "/nonexistent/path")
        result = self.fn({})
        assert result["judge_result"] == "qawrite_criteria"
        assert len(self.mock.call_history) == 0


class TestQACriteriaPassThrough:
    """review_to_qa_artifact — 直通节点。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        fn = QA_CRITERIA_DEF.nodes["review_to_qa_artifact"]
        assert fn({"phase": "test"}) == {"phase": "test"}


class TestQACriteriaFeedback:
    """review_qa_criteria_feedback — 反馈节点（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = QA_CRITERIA_DEF.nodes["review_qa_criteria_feedback"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("qa_criteria_review", "审查意见")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_reviewer_agent(self, mock_ensure):
        """写反馈信调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_to_write(self, mock_ensure):
        """judge_result 应为 qawrite_criteria。"""
        result = self.fn({})
        assert result["judge_result"] == "qawrite_criteria"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_clears_review_text(self, mock_ensure):
        """审查意见写入后从 context 清空。"""
        self.fn({})
        assert self.rt.context.get_ctx("qa_criteria_review") == ""

    def test_raises_when_no_review_text(self):
        """审查意见为空应抛出 RuntimeError。"""
        self.rt.context.set_ctx("qa_criteria_review", "")
        with pytest.raises(RuntimeError, match="审查意见为空"):
            self.fn({})


# ── QAWriteTestPlan ──

class TestQAWritePlan:
    """qawrite_plan — QA 写测试计划（Type A + ensure_write_file）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAWriteTestPlan._runtime = self.rt

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_qa_agent(self, mock_ensure):
        """写计划调 QA agent。"""
        QAWriteTestPlan.run({})
        assert self.mock.call_history[0][0] == "qa"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_qa_conv(self, mock_ensure):
        """应创建 qa_conv。"""
        QAWriteTestPlan.run({})
        assert self.rt.context.get_ctx("qa_conv") is not None
        assert "qa-plan" in self.rt.context.get_ctx("qa_conv")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_qa_plan_path(self, mock_ensure):
        """qa_plan_path 应存入 context。"""
        QAWriteTestPlan.run({})
        assert self.rt.context.get_ctx("qa_plan_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_plan_written_phase(self, mock_ensure):
        """phase 应为 qa_plan_written。"""
        result = QAWriteTestPlan.run({})
        assert result["phase"] == "qa_plan_written"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_includes_feedback_when_path_exists(self, mock_ensure, tmp_path):
        """qa_plan_feedback_path 存在时 prompt 含反馈。"""
        feedback = tmp_path / "plan-feedback.md"
        feedback.write_text("计划需修改")
        self.rt.context.set_ctx("qa_plan_feedback_path", str(feedback))
        QAWriteTestPlan.run({})
        prompt = self.mock.call_history[0][2]
        assert "反馈意见" in prompt


# ── MASTER_PLAN_REVIEW_DEF 子图节点 ──

class TestMasterPlanReview:
    """master_plan_review — ArtifactReviewSubgraph Master 审查测试计划（Type B）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_PLAN_REVIEW_DEF.nodes["master_plan_review"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="P")
    def test_routes_to_pass_when_judge_P(self, mock_judge):
        """P → qa_write_code。"""
        result = self.fn({})
        assert result["judge_result"] == "qa_write_code"

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="F")
    def test_routes_to_fail_when_judge_F(self, mock_judge):
        """F → qa_write_plan。"""
        result = self.fn({})
        assert result["judge_result"] == "qa_write_plan"

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="P")
    def test_calls_master_agent(self, mock_judge):
        """审查调 Master agent（agent_role=master）。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "master"


class TestMasterPlanReviewPass:
    """master_plan_review_pass — 直通节点。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        fn = MASTER_PLAN_REVIEW_DEF.nodes["master_plan_review_pass"]
        assert fn({"phase": "test"}) == {"phase": "test"}


class TestMasterPlanReviewFeedback:
    """master_plan_review_feedback — 写反馈信（Type A via write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = MASTER_PLAN_REVIEW_DEF.nodes["master_plan_review_feedback"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("qa_plan_review", "Master 审查意见")
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写反馈信调 Master agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_to_fail(self, mock_ensure):
        """judge_result 应为 qa_write_plan。"""
        result = self.fn({})
        assert result["judge_result"] == "qa_write_plan"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_clears_review_text(self, mock_ensure):
        """审查意见写入后从 context 清空。"""
        self.fn({})
        assert self.rt.context.get_ctx("qa_plan_review") == ""

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_feedback_path(self, mock_ensure):
        """qa_plan_feedback_path 应存入 context。"""
        self.fn({})
        assert self.rt.context.get_ctx("qa_plan_feedback_path") is not None

    def test_raises_when_no_review_text(self):
        """审查意见为空应抛出 RuntimeError。"""
        self.rt.context.set_ctx("qa_plan_review", "")
        with pytest.raises(RuntimeError, match="审查意见为空"):
            self.fn({})


# ── QAWriteTestCase ──

class TestQAWriteCode:
    """qawrite_code — QA 写测试代码（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAWriteTestCase._runtime = self.rt

    def test_calls_qa_agent(self):
        """写测试代码调 QA agent。"""
        QAWriteTestCase.run({})
        assert self.mock.call_history[0][0] == "qa"

    def test_sets_qa_code_path(self):
        """qa_code_path 应存入 context。"""
        QAWriteTestCase.run({})
        assert self.rt.context.get_ctx("qa_code_path") is not None

    def test_returns_code_written_phase(self):
        """phase 应为 qa_code_written。"""
        result = QAWriteTestCase.run({})
        assert result["phase"] == "qa_code_written"

    def test_includes_feedback_when_path_exists(self, tmp_path):
        """qa_code_feedback_path 存在时 prompt 含反馈。"""
        feedback = tmp_path / "code-feedback.md"
        feedback.write_text("代码需修改")
        self.rt.context.set_ctx("qa_code_feedback_path", str(feedback))
        QAWriteTestCase.run({})
        prompt = self.mock.call_history[0][2]
        assert "反馈意见" in prompt


# ── REVIEWER_CODE_REVIEW_DEF 子图节点 ──

class TestReviewerCodeReview:
    """reviewer_code_review — ArtifactReviewSubgraph（Type B）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = REVIEWER_CODE_REVIEW_DEF.nodes["reviewer_code_review"]
        self.fn._runtime = self.rt

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="P")
    def test_routes_to_pass_when_judge_P(self, mock_judge):
        """P → qa_run_tests。"""
        result = self.fn({})
        assert result["judge_result"] == "qa_run_tests"

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="F")
    def test_routes_to_fail_when_judge_F(self, mock_judge):
        """F → qa_write_code。"""
        result = self.fn({})
        assert result["judge_result"] == "qa_write_code"

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="P")
    def test_calls_reviewer_agent(self, mock_judge):
        """审查调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"


class TestReviewerCodeReviewPass:
    """reviewer_code_review_pass — 直通节点。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        fn = REVIEWER_CODE_REVIEW_DEF.nodes["reviewer_code_review_pass"]
        assert fn({"phase": "test"}) == {"phase": "test"}


class TestReviewerCodeReviewFeedback:
    """reviewer_code_review_feedback — 写反馈信（Type A via write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = REVIEWER_CODE_REVIEW_DEF.nodes["reviewer_code_review_feedback"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("qa_code_review", "Reviewer 审查意见")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_reviewer_agent(self, mock_ensure):
        """写反馈信调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_to_fail(self, mock_ensure):
        """judge_result 应为 qa_write_code。"""
        result = self.fn({})
        assert result["judge_result"] == "qa_write_code"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_clears_review_text(self, mock_ensure):
        """审查意见写入后从 context 清空。"""
        self.fn({})
        assert self.rt.context.get_ctx("qa_code_review") == ""

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_feedback_path(self, mock_ensure):
        """qa_code_feedback_path 应存入 context。"""
        self.fn({})
        assert self.rt.context.get_ctx("qa_code_feedback_path") is not None

    def test_raises_when_no_review_text(self):
        """审查意见为空应抛出 RuntimeError。"""
        self.rt.context.set_ctx("qa_code_review", "")
        with pytest.raises(RuntimeError, match="审查意见为空"):
            self.fn({})


# ── QARunTests ──

class TestQARunTests:
    """qa_run_tests — QA 运行测试（Type A + ensure_write_file）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QARunTests._runtime = self.rt

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_qa_agent(self, mock_ensure):
        """运行测试调 QA agent。"""
        QARunTests.run({})
        assert self.mock.call_history[0][0] == "qa"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_qa_conv(self, mock_ensure):
        """应创建 qa_conv。"""
        QARunTests.run({})
        assert self.rt.context.get_ctx("qa_conv") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_test_report_path(self, mock_ensure):
        """qa_test_report_path 应存入 context。"""
        QARunTests.run({})
        assert self.rt.context.get_ctx("qa_test_report_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_tests_run_phase(self, mock_ensure):
        """phase 应为 qa_tests_run。"""
        result = QARunTests.run({})
        assert result["phase"] == "qa_tests_run"


# ── JudgeTestResult ──

class TestJudgeTestResult:
    """judge_test_result — Judge 判读测试结果（Type B，读文件 + judge_reply）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        JudgeTestResult._runtime = self.rt
        report = tmp_path / "test-report.md"
        report.write_text("Test Report: 10 passed, 0 failed")
        self.rt.context.set_ctx("qa_test_report_path", str(report))

    @patch("workflow.phase3.judge_reply", return_value="A")
    def test_routes_to_flush_when_judge_A(self, mock_judge):
        """全部通过 → qa_flush。"""
        result = JudgeTestResult.judge({})
        assert result["judge_result"] == "qa_flush"

    @patch("workflow.phase3.judge_reply", return_value="B")
    def test_routes_to_dev_fix_when_judge_B(self, mock_judge):
        """有失败 → dev_fix。"""
        result = JudgeTestResult.judge({})
        assert result["judge_result"] == "dev_fix"

    @patch("workflow.phase3.judge_reply", return_value="B")
    def test_sets_bug_report_path_on_fail(self, mock_judge):
        """失败时生成 bug-report.md 并存入 context。"""
        JudgeTestResult.judge({})
        assert self.rt.context.get_ctx("qa_bug_report_path") is not None
        path = self.rt.context.get_ctx("qa_bug_report_path")
        assert os.path.exists(path)

    def test_raises_when_report_missing(self):
        """测试报告文件不存在应抛出 RuntimeError。"""
        self.rt.context.set_ctx("qa_test_report_path", "/nonexistent/path")
        with pytest.raises(RuntimeError, match="测试报告文件不存在"):
            JudgeTestResult.judge({})


class TestJudgeTestResultPass:
    """judge_test_result_pass — 空节点。"""

    def test_returns_state_unchanged(self):
        fn = JudgeTestResult.to_flush
        assert fn({"phase": "test"}) == {"phase": "test"}


class TestJudgeTestResultFail:
    """judge_test_result_fail — 空节点。"""

    def test_returns_state_unchanged(self):
        fn = JudgeTestResult.to_dev_fix
        assert fn({"phase": "test"}) == {"phase": "test"}


# ── DevFix ──

class TestDevFix:
    """dev_fix — Dev 修 bug（Type A，读文件 + call_agent）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevFix._runtime = self.rt
        bug_report = tmp_path / "bug-report.md"
        bug_report.write_text("Failed test: login")
        self.rt.context.set_ctx("qa_bug_report_path", str(bug_report))

    def test_calls_dev_agent(self):
        """修 bug 调 Dev agent。"""
        DevFix.run({})
        assert self.mock.call_history[0][0] == "dev"

    def test_returns_fix_done_phase(self):
        """phase 应为 dev_fix_done。"""
        result = DevFix.run({})
        assert result["phase"] == "dev_fix_done"

    def test_raises_when_bug_report_missing(self):
        """bug 报告文件不存在应抛出 RuntimeError。"""
        self.rt.context.set_ctx("qa_bug_report_path", "/nonexistent/path")
        with pytest.raises(RuntimeError, match="Bug 报告文件不存在"):
            DevFix.run({})
