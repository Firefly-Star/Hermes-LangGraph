"""Phase 2 节点测试：DevAlign / DEV_CRITERIA_DEF / DevWriteDesign。"""
from __future__ import annotations
from unittest.mock import patch
import os
import pytest
from agent_runtime import AgentRuntime
from workflow.phase2 import (DEV_HANDOFF_DEF, DevAlign, DEV_CRITERIA_DEF,
                             DevWriteDesign,
                             DEV_DESIGN_REVIEW_DEF, WriteDesignSummary,
                             DevWritePlan,
                             DEV_PLAN_REVIEW_DEF, DevGitInit,
                             DevExecStep, DevReviewStep,
                             DevCommit)
from workflow.utils import count_steps


# ── DevAlign ──


class TestDevAlignDev:
    """dev_align_dev — Type C（letter 读写），第一轮 Handoff / 后续反馈轮。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevAlign._runtime = self.rt

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_first_round_calls_dev_agent(self, mock_ensure, tmp_path):
        """第一轮从 handoff 信件读取，调 Dev agent。"""
        letter = tmp_path / "devletter.md"
        letter.write_text("Master 给 Dev 的信")
        self.rt.context.set_ctx("devletter_path", str(letter))
        DevAlign.dev({})
        assert self.mock.call_history[0][0] == "dev"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_first_round_sets_dev_conv(self, mock_ensure, tmp_path):
        """第一轮应创建 dev_conv（含 dev-align 标记）。"""
        letter = tmp_path / "devletter.md"
        letter.write_text("内容")
        self.rt.context.set_ctx("devletter_path", str(letter))
        DevAlign.dev({})
        conv = self.rt.context.get_ctx("dev_conv")
        assert conv is not None
        assert "dev-align" in conv

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_feedback_round_calls_dev_agent(self, mock_ensure, tmp_path):
        """反馈轮从反馈信件读取，调 Dev agent。"""
        feedback = tmp_path / "feedback.md"
        feedback.write_text("上轮反馈意见")
        self.rt.context.set_ctx("dev_feedback_path", str(feedback))
        DevAlign.dev({})
        assert self.mock.call_history[0][0] == "dev"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_feedback_round_reuses_dev_conv(self, mock_ensure, tmp_path):
        """反馈轮复用已有的 dev_conv。"""
        self.rt.context.set_ctx("dev_conv", "dev-align-existing")
        feedback = tmp_path / "feedback.md"
        feedback.write_text("意见")
        self.rt.context.set_ctx("dev_feedback_path", str(feedback))
        DevAlign.dev({})
        assert self.rt.context.get_ctx("dev_conv") == "dev-align-existing"

    def test_raises_when_no_handoff_path(self):
        """第一轮没有 devletter_path 应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="没有 handoff 信件路径"):
            DevAlign.dev({})

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_dev_done_phase(self, mock_ensure, tmp_path):
        """完成后 phase 应为 dev_align_dev_done。"""
        letter = tmp_path / "devletter.md"
        letter.write_text("内容")
        self.rt.context.set_ctx("devletter_path", str(letter))
        result = DevAlign.dev({})
        assert result["phase"] == "dev_align_dev_done"


class TestDevAlignPM:
    """dev_align_pm — Type C（letter 读写），PM 读 Dev 理解写回复。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevAlign._runtime = self.rt
        dev_reply = tmp_path / "dev-understanding.md"
        dev_reply.write_text("Dev 的理解总结")
        self.rt.context.set_ctx("dev_reply_path", str(dev_reply))

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_pm_agent(self, mock_ensure):
        """调 PM agent 写回复。"""
        DevAlign.pm({})
        assert self.mock.call_history[0][0] == "pm"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_pm_conv(self, mock_ensure):
        """应创建 pm_conv（含 pm-align 标记）。"""
        DevAlign.pm({})
        conv = self.rt.context.get_ctx("pm_conv")
        assert conv is not None
        assert "pm-align" in conv

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_pm_reply_path_in_context(self, mock_ensure):
        """pm_reply_path 应存入 context。"""
        DevAlign.pm({})
        assert self.rt.context.get_ctx("pm_reply_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_pm_done_phase(self, mock_ensure):
        """完成后 phase 应为 dev_align_pm_done。"""
        result = DevAlign.pm({})
        assert result["phase"] == "dev_align_pm_done"

    def test_raises_when_dev_reply_missing(self):
        """dev_reply_path 文件不存在应抛出 RuntimeError。"""
        self.rt.context.set_ctx("dev_reply_path", "/nonexistent/path")
        with pytest.raises(RuntimeError, match="Dev 理解信件不存在"):
            DevAlign.pm({})


class TestDevAlignJudge:
    """dev_align_judge — Type B（路由），judge_reply + ❓ 特殊路径。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevAlign._runtime = self.rt

    @patch("workflow.phase2.judge_reply", return_value="C")
    def test_routes_to_master_when_judge_C(self, mock_judge):
        """judge 返回 C（需升级）时路由到 master。"""
        self.rt.context.set_ctx("pm_reply_text", "PM 有无法回答的问题")
        result = DevAlign.judge({})
        assert result["judge_result"] == "dev_align_master"

    @patch("workflow.phase2.judge_reply", return_value="A")
    def test_routes_to_master_when_needs_upgrade(self, mock_judge):
        """PM 回复含 ❓ 时路由到 master（即使 judge 返回 A）。"""
        self.rt.context.set_ctx("pm_reply_text", "有问题需要升级❓给 Master")
        result = DevAlign.judge({})
        assert result["judge_result"] == "dev_align_master"

    @patch("workflow.phase2.judge_reply", return_value="B")
    def test_routes_to_dev_when_judge_B(self, mock_judge):
        """judge 返回 B（需反馈）时路由回 dev。"""
        self.rt.context.set_ctx("pm_reply_text", "需要 Dev 修改理解")
        result = DevAlign.judge({})
        assert result["judge_result"] == "dev_align_dev"

    @patch("workflow.phase2.judge_reply", return_value="B")
    def test_sets_dev_feedback_path_when_judge_B(self, mock_judge, tmp_path):
        """judge 返回 B 时设置 dev_feedback_path 为 pm_reply_path。"""
        pm_reply = tmp_path / "pm-reply.md"
        pm_reply.write_text("需要修改")
        self.rt.context.set_ctx("pm_reply_text", "需要修改")
        self.rt.context.set_ctx("pm_reply_path", str(pm_reply))
        DevAlign.judge({})
        assert self.rt.context.get_ctx("dev_feedback_path") == str(pm_reply)

    @patch("workflow.phase2.judge_reply", return_value="A")
    def test_routes_to_exit_when_judge_A(self, mock_judge):
        """judge 返回 A（通过）时路由到 exit。"""
        self.rt.context.set_ctx("pm_reply_text", "全部正确")
        result = DevAlign.judge({})
        assert result["judge_result"] == "exit"

    @patch("workflow.phase2.judge_reply", return_value="A")
    def test_deletes_pm_reply_on_exit(self, mock_judge, tmp_path):
        """exit 路径应删除 pm_reply 文件。"""
        pm_reply = tmp_path / "pm-reply.md"
        pm_reply.write_text("全部正确")
        self.rt.context.set_ctx("pm_reply_text", "全部正确")
        self.rt.context.set_ctx("pm_reply_path", str(pm_reply))
        DevAlign.judge({})
        assert not os.path.exists(pm_reply)


class TestDevAlignMaster:
    """dev_align_master — Type A+B: call_agent + judge_reply 混合。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevAlign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        pm_reply = tmp_path / "pm-reply.md"
        pm_reply.write_text("PM 报告内容")
        self.rt.context.set_ctx("pm_reply_path", str(pm_reply))

    @patch("workflow.phase2.judge_reply", return_value="A")
    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure, mock_judge):
        """调 Master agent 处理升级问题。"""
        DevAlign.master({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.phase2.judge_reply", return_value="A")
    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_routes_to_dev_when_judge_A(self, mock_ensure, mock_judge):
        """judge 返回 A 时路由回 dev。"""
        result = DevAlign.master({})
        assert result["judge_result"] == "dev_align_dev"

    @patch("workflow.phase2.judge_reply", return_value="B")
    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_routes_to_confirm_when_judge_B(self, mock_ensure, mock_judge):
        """judge 返回 B（需用户确认）时路由到 confirm。"""
        result = DevAlign.master({})
        assert result["judge_result"] == "dev_align_confirm"

    def test_raises_when_pm_reply_missing(self):
        """pm_reply_path 不存在应抛出 RuntimeError。"""
        self.rt.context.set_ctx("pm_reply_path", "/nonexistent/path")
        with pytest.raises(RuntimeError, match="PM 回复信件不存在"):
            DevAlign.master({})


class TestDevAlignConfirm:
    """dev_align_confirm — Type D，clarify_loop 用户确认。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevAlign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.phase2.clarify_loop", return_value="用户确认")
    def test_calls_clarify_loop(self, mock_loop):
        """调 clarify_loop 进行用户确认。"""
        DevAlign.confirm({})
        mock_loop.assert_called_once()

    @patch("workflow.phase2.clarify_loop", return_value="用户确认")
    def test_uses_master_conv(self, mock_loop):
        """clarify_loop 使用 master_conv。"""
        DevAlign.confirm({})
        assert mock_loop.call_args[0][1] == "master-test"

    @patch("workflow.phase2.clarify_loop", return_value="用户确认")
    def test_returns_confirmed_phase(self, mock_loop):
        """完成后 phase 应为 dev_align_confirmed。"""
        result = DevAlign.confirm({})
        assert result["phase"] == "dev_align_confirmed"


class TestDevAlignRecord:
    """dev_align_record — Type A，Master 记录决策到 project_context。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevAlign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "path/to/project_context.md")

    def test_calls_master_agent(self):
        """调 Master agent 记录决策。"""
        DevAlign.record({})
        assert self.mock.call_history[0][0] == "master"

    def test_prompt_contains_project_context_path(self):
        """prompt 中包含 project_context.md 路径。"""
        DevAlign.record({})
        prompt = self.mock.call_history[0][2]
        assert "project_context.md" in prompt

    def test_returns_recorded_phase(self):
        """完成后 phase 应为 dev_align_recorded。"""
        result = DevAlign.record({})
        assert result["phase"] == "dev_align_recorded"


class TestDevAlignFinal:
    """dev_align_final — Type A，Master 写最终答复（write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevAlign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """调 Master agent 写最终答复信。"""
        DevAlign.final({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_dev_feedback_path(self, mock_ensure):
        """dev_feedback_path 应设为最终答复的路径。"""
        DevAlign.final({})
        assert self.rt.context.get_ctx("dev_feedback_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_final_done_phase(self, mock_ensure):
        """完成后 phase 应为 dev_align_final_done，judge 指向 dev。"""
        result = DevAlign.final({})
        assert result["phase"] == "dev_align_final_done"
        assert result["judge_result"] == "dev_align_dev"


class TestDevAlignJudgeExit:
    """dev_align_judge_exit — 空节点，state 直通。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        state = {"phase": "test", "data": "value"}
        assert DevAlign.judge_exit(state) == state


# ── DEV_HANDOFF_DEF 子图节点 ──

class TestDevHandoff:
    """dev_handoff — HandoffSubgraph Master 写给 Dev 的信（Type A via write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = DEV_HANDOFF_DEF.nodes["dev_handoff"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写信调 Master agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_uses_master_conv_from_context(self, mock_ensure):
        """写信 conversation 应来自 master_conv。"""
        self.fn({})
        assert self.mock.call_history[0][1] == "master-test"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_stores_devletter_path_in_context(self, mock_ensure):
        """devletter_path 应存入 context。"""
        self.fn({})
        assert self.rt.context.get_ctx("devletter_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_handoff_done_phase(self, mock_ensure):
        """phase 应为 dev_handoff_done。"""
        result = self.fn({})
        assert result["phase"] == "dev_handoff_done"

    def test_raises_when_no_master_conv(self):
        """master_conv 不存在时应抛出 RuntimeError。"""
        self.rt.context.set_ctx("master_conv", "")
        with pytest.raises(RuntimeError, match="master_conv 对话不存在"):
            self.fn({})


# ── DEV_CRITERIA_DEF 子图节点 ──

class TestDevCriteriaWrite:
    """devwrite_criteria — CriteriaDefinitionSubgraph 写标准节点（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = DEV_CRITERIA_DEF.nodes["devwrite_criteria"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写标准调 Master agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_criteria_done_phase(self, mock_ensure):
        """完成后 phase 应为 dev_criteria_done。"""
        result = self.fn({})
        assert result["phase"] == "dev_criteria_done"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_judge_result_routes_to_review(self, mock_ensure):
        """judge_result 应为 review_dev_criteria。"""
        result = self.fn({})
        assert result["judge_result"] == "review_dev_criteria"


class TestDevCriteriaReview:
    """review_dev_criteria — CriteriaDefinitionSubgraph 审查节点（Type B）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = DEV_CRITERIA_DEF.nodes["review_dev_criteria"]
        self.fn._runtime = self.rt
        criteria_file = tmp_path / "criteria-design.md"
        criteria_file.write_text("Dev 设计审核标准")
        self.rt.context.set_ctx("dev_criteria_path", str(criteria_file))

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="P")
    def test_routes_to_pass_when_judge_P(self, mock_judge):
        """judge 返回 P 时 judge_result 为 dev_write_design。"""
        result = self.fn({})
        assert result["judge_result"] == "dev_write_design"

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="F")
    def test_routes_to_fail_when_judge_F(self, mock_judge):
        """judge 返回 F 时 judge_result 为 devwrite_criteria。"""
        result = self.fn({})
        assert result["judge_result"] == "devwrite_criteria"

    @patch("workflow.subgraphs.criteria_definition.judge_reply", return_value="P")
    def test_calls_reviewer_agent(self, mock_judge):
        """审查调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"

    def test_returns_early_when_no_criteria_file(self):
        """标准文件不存在时直接返回 fail，不调 agent。"""
        self.rt.context.set_ctx("dev_criteria_path", "/nonexistent/path")
        result = self.fn({})
        assert result["judge_result"] == "devwrite_criteria"
        assert len(self.mock.call_history) == 0


class TestDevCriteriaPassThrough:
    """review_to_dev_artifact — 直通节点。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        state = {"phase": "test"}
        fn = DEV_CRITERIA_DEF.nodes["review_to_dev_artifact"]
        assert fn(state) == state


class TestDevCriteriaFeedback:
    """review_dev_criteria_feedback — 反馈节点（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = DEV_CRITERIA_DEF.nodes["review_dev_criteria_feedback"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("dev_criteria_review", "审查意见")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_reviewer_agent(self, mock_ensure):
        """写反馈信调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_to_write_judge_result(self, mock_ensure):
        """完成后 judge_result 应为 devwrite_criteria。"""
        result = self.fn({})
        assert result["judge_result"] == "devwrite_criteria"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_clears_review_text_from_context(self, mock_ensure):
        """审查意见写入反馈信后应从 context 清空。"""
        self.fn({})
        assert self.rt.context.get_ctx("dev_criteria_review") == ""

    def test_raises_when_no_review_text(self):
        """审查意见为空时应抛出 RuntimeError。"""
        self.rt.context.set_ctx("dev_criteria_review", "")
        with pytest.raises(RuntimeError, match="审查意见为空"):
            self.fn({})


# ── DevWriteDesign ──

class TestDevWriteDesignWrite:
    """dev_write_design_letter — Master 写设计指令信（Type A via write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevWriteDesign._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写设计指令信调 Master agent。"""
        DevWriteDesign.write_design_letter({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_design_paths_in_context(self, mock_ensure):
        """designletter_path 和 design_path 存入 context。"""
        DevWriteDesign.write_design_letter({})
        assert self.rt.context.get_ctx("designletter_path") is not None
        assert self.rt.context.get_ctx("design_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_design_letter_done_phase(self, mock_ensure):
        """phase 应为 dev_design_letter_done。"""
        result = DevWriteDesign.write_design_letter({})
        assert result["phase"] == "dev_design_letter_done"

    def test_raises_when_no_master_conv(self):
        """master_conv 不存在应抛出 RuntimeError。"""
        self.rt.context.set_ctx("master_conv", "")
        with pytest.raises(RuntimeError, match="master conversation 不存在"):
            DevWriteDesign.write_design_letter({})

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_dev_conv(self, mock_ensure):
        """创建 dev_conv（含 dev-design 标记）。"""
        DevWriteDesign.write_design_letter({})
        conv = self.rt.context.get_ctx("dev_conv")
        assert conv is not None
        assert "dev-design" in conv

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_includes_feedback_when_path_exists(self, mock_ensure, tmp_path):
        """design_feedback_path 存在时 prompt 应包含反馈意见。"""
        feedback = tmp_path / "feedback.md"
        feedback.write_text("设计需要修改")
        self.rt.context.set_ctx("design_feedback_path", str(feedback))
        DevWriteDesign.write_design_letter({})
        prompt = self.mock.call_history[0][2]
        assert "反馈意见" in prompt

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_deletes_feedback_file_after_use(self, mock_ensure, tmp_path):
        """使用后应删除 feedback 文件。"""
        feedback = tmp_path / "feedback.md"
        feedback.write_text("反馈")
        self.rt.context.set_ctx("design_feedback_path", str(feedback))
        DevWriteDesign.write_design_letter({})
        assert not os.path.exists(feedback)

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_reuses_existing_dev_conv(self, mock_ensure):
        """已有的 dev_conv 应复用不被覆盖。"""
        self.rt.context.set_ctx("dev_conv", "dev-design-existing")
        DevWriteDesign.write_design_letter({})
        assert self.rt.context.get_ctx("dev_conv") == "dev-design-existing"


class TestDevWriteDesignRead:
    """dev_write_design_read — Dev 读信写设计文档（Type A via read_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevWriteDesign._runtime = self.rt
        self.rt.context.set_ctx("dev_conv", "dev-test")
        letter = tmp_path / "design-letter.md"
        letter.write_text("设计说明")
        self.rt.context.set_ctx("designletter_path", str(letter))
        self.rt.context.set_ctx("design_path", str(tmp_path / "Dev" / "design.md"))

    def test_calls_dev_agent(self):
        """调 Dev agent 读信写设计。"""
        DevWriteDesign.read_design_letter({})
        assert self.mock.call_history[0][0] == "dev"

    def test_returns_design_done_phase(self):
        """phase 应为 dev_design_done，judge 为 pass。"""
        result = DevWriteDesign.read_design_letter({})
        assert result["phase"] == "dev_design_done"
        assert result["judge_result"] == "pass"


# ── DEV_DESIGN_REVIEW_DEF 子图节点 ──

class TestDevDesignReview:
    """dev_design_review — ArtifactReviewSubgraph 审查节点（Type B）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = DEV_DESIGN_REVIEW_DEF.nodes["dev_design_review"]
        self.fn._runtime = self.rt

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="P")
    def test_routes_to_pass_when_judge_P(self, mock_judge):
        """judge 返回 P 时 judge_result 为 dev_write_plan。"""
        result = self.fn({})
        assert result["judge_result"] == "dev_write_plan"

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="F")
    def test_routes_to_fail_when_judge_F(self, mock_judge):
        """judge 返回 F 时 judge_result 为 dev_write_design。"""
        result = self.fn({})
        assert result["judge_result"] == "dev_write_design"

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="P")
    def test_calls_reviewer_agent(self, mock_judge):
        """审查调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"


class TestDevDesignReviewPass:
    """dev_design_review_pass — 直通节点。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        state = {"phase": "test"}
        fn = DEV_DESIGN_REVIEW_DEF.nodes["dev_design_review_pass"]
        assert fn(state) == state


class TestDevDesignReviewFeedback:
    """dev_design_review_feedback — 写反馈信（Type A via write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = DEV_DESIGN_REVIEW_DEF.nodes["dev_design_review_feedback"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("review_text", "设计审查意见")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_reviewer_agent(self, mock_ensure):
        """写反馈信调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_to_fail_judge_result(self, mock_ensure):
        """完成后 judge_result 为 dev_write_design。"""
        result = self.fn({})
        assert result["judge_result"] == "dev_write_design"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_clears_review_text_from_context(self, mock_ensure):
        """审查意见写入后从 context 清空。"""
        self.fn({})
        assert self.rt.context.get_ctx("review_text") == ""

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_design_feedback_path(self, mock_ensure):
        """design_feedback_path 应存入 context。"""
        self.fn({})
        assert self.rt.context.get_ctx("design_feedback_path") is not None

    def test_raises_when_no_review_text(self):
        """审查意见为空时应抛出 RuntimeError。"""
        self.rt.context.set_ctx("review_text", "")
        with pytest.raises(RuntimeError, match="审查意见为空"):
            self.fn({})


# ── WriteDesignSummary ──

class TestWriteDesignSummary:
    """write_design_summary — Dev 生成 design-summary + design-index（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        WriteDesignSummary._runtime = self.rt
        self.rt.context.set_ctx("dev_conv", "dev-test")
        # 创建设计文档，让 agent "读"
        dev_dir = os.path.join(self.rt.paths.workspace, "Dev")
        os.makedirs(dev_dir, exist_ok=True)
        design_path = os.path.join(dev_dir, "design.md")
        open(design_path, "w", encoding="utf-8").write("# 设计文档")

    @patch("workflow.phase2.ensure_write_file", return_value=True)
    def test_calls_dev_agent(self, mock_ensure):
        """调 Dev agent 读 design.md 生成概要+索引。"""
        WriteDesignSummary.run({})
        assert self.mock.call_history[0][0] == "dev"

    @patch("workflow.phase2.ensure_write_file", return_value=True)
    def test_prompt_contains_keywords(self, mock_ensure):
        """prompt 包含 design-summary 和 design-index 路径及要求。"""
        WriteDesignSummary.run({})
        prompt = self.mock.call_history[0][2]
        assert "design-summary.md" in prompt
        assert "design-index.md" in prompt

    @patch("workflow.phase2.ensure_write_file", return_value=True)
    def test_calls_ensure_write_file_twice(self, mock_ensure):
        """概要文件和索引文件都需要 ensure_write_file。"""
        WriteDesignSummary.run({})
        assert mock_ensure.call_count == 2

    @patch("workflow.phase2.ensure_write_file", return_value=True)
    def test_returns_design_summary_done(self, mock_ensure):
        """phase 应为 design_summary_done。"""
        result = WriteDesignSummary.run({})
        assert result["phase"] == "design_summary_done"
        assert result["judge_result"] == "pass"


# ── DevWritePlan ──

class TestDevWritePlanWrite:
    """dev_write_plan_letter — Master 写计划指令信（Type A via write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevWritePlan._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """写计划指令信调 Master agent。"""
        DevWritePlan.write_plan_letter({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_plan_paths_in_context(self, mock_ensure):
        """planletter_path 和 plan_path 存入 context。"""
        DevWritePlan.write_plan_letter({})
        assert self.rt.context.get_ctx("planletter_path") is not None
        assert self.rt.context.get_ctx("plan_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_plan_letter_done_phase(self, mock_ensure):
        """phase 应为 dev_plan_letter_done。"""
        result = DevWritePlan.write_plan_letter({})
        assert result["phase"] == "dev_plan_letter_done"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_dev_conv(self, mock_ensure):
        """创建 dev_conv（含 dev-plan 标记）。"""
        DevWritePlan.write_plan_letter({})
        conv = self.rt.context.get_ctx("dev_conv")
        assert conv is not None
        assert "dev-plan" in conv

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_includes_feedback_when_path_exists(self, mock_ensure, tmp_path):
        """plan_feedback_path 存在时 prompt 包含反馈意见。"""
        feedback = tmp_path / "plan-feedback.md"
        feedback.write_text("计划需要修改")
        self.rt.context.set_ctx("plan_feedback_path", str(feedback))
        DevWritePlan.write_plan_letter({})
        prompt = self.mock.call_history[0][2]
        assert "反馈意见" in prompt

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_reuses_existing_dev_conv(self, mock_ensure):
        """已有的 dev_conv 应复用。"""
        self.rt.context.set_ctx("dev_conv", "dev-plan-existing")
        DevWritePlan.write_plan_letter({})
        assert self.rt.context.get_ctx("dev_conv") == "dev-plan-existing"


class TestDevWritePlanRead:
    """dev_write_plan_read — Dev 读信写计划文档（Type A via read_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevWritePlan._runtime = self.rt
        self.rt.context.set_ctx("dev_conv", "dev-test")
        letter = tmp_path / "plan-letter.md"
        letter.write_text("计划说明")
        self.rt.context.set_ctx("planletter_path", str(letter))
        self.rt.context.set_ctx("plan_path", str(tmp_path / "Dev" / "plan.md"))

    def test_calls_dev_agent(self):
        """调 Dev agent 读信写计划。"""
        DevWritePlan.read_plan_letter({})
        assert self.mock.call_history[0][0] == "dev"

    def test_returns_plan_done_phase(self):
        """phase 应为 dev_plan_done，judge 为 pass。"""
        result = DevWritePlan.read_plan_letter({})
        assert result["phase"] == "dev_plan_done"
        assert result["judge_result"] == "pass"


# ── DEV_PLAN_REVIEW_DEF 子图节点 ──

class TestDevPlanReview:
    """dev_plan_review — ArtifactReviewSubgraph 审查节点，含 on_pass 回调。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = DEV_PLAN_REVIEW_DEF.nodes["dev_plan_review"]
        self.fn._runtime = self.rt
        # on_pass 回调需要 plan.md 来 count_steps
        plan_dir = os.path.join(self.rt.paths.workspace, "Dev")
        os.makedirs(plan_dir, exist_ok=True)
        plan_path = os.path.join(plan_dir, "plan.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write("## Step 1: 实现功能 A\n内容\n## Step 2: 实现功能 B\n内容")
        self.plan_path = plan_path

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="P")
    def test_routes_to_pass_when_judge_P(self, mock_judge):
        """judge 返回 P 时调用 on_pass，judge_result 为 dev_exec。"""
        result = self.fn({})
        assert result["judge_result"] == "dev_exec"

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="P")
    def test_on_pass_sets_step_counters(self, mock_judge):
        """PASS 时 on_pass 初始化 step 计数器。"""
        self.fn({})
        assert self.rt.context.get_ctx("dev_step_index") == "0"
        assert self.rt.context.get_ctx("dev_total_steps") == "2"
        assert self.rt.context.get_ctx("dev_step_fail_count") == "0"
        assert self.rt.context.get_ctx("dev_step_has_failed") == "false"

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="F")
    def test_routes_to_fail_when_judge_F(self, mock_judge):
        """judge 返回 F 时 judge_result 为 dev_write_plan。"""
        result = self.fn({})
        assert result["judge_result"] == "dev_write_plan"

    @patch("workflow.subgraphs.artifact_review.judge_reply", return_value="P")
    def test_calls_reviewer_agent(self, mock_judge):
        """审查调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"

    def test_returns_review_done_phase_when_P(self):
        """P 但无 on_pass 时不走此路径（DEV_PLAN_REVIEW 有 on_pass）。"""
        pass  # on_pass 逻辑已在 test_on_pass_sets_step_counters 覆盖


class TestDevPlanReviewPass:
    """dev_plan_review_pass — 直通节点。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        state = {"phase": "test"}
        fn = DEV_PLAN_REVIEW_DEF.nodes["dev_plan_review_pass"]
        assert fn(state) == state


class TestDevPlanReviewFeedback:
    """dev_plan_review_feedback — 写反馈信（Type A via write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        self.fn = DEV_PLAN_REVIEW_DEF.nodes["dev_plan_review_feedback"]
        self.fn._runtime = self.rt
        self.rt.context.set_ctx("review_text", "计划审查意见")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_reviewer_agent(self, mock_ensure):
        """写反馈信调 Reviewer agent。"""
        self.fn({})
        assert self.mock.call_history[0][0] == "reviewer"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_to_fail_judge_result(self, mock_ensure):
        """完成后 judge_result 为 dev_write_plan。"""
        result = self.fn({})
        assert result["judge_result"] == "dev_write_plan"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_clears_review_text_from_context(self, mock_ensure):
        """审查意见写入后从 context 清空。"""
        self.fn({})
        assert self.rt.context.get_ctx("review_text") == ""

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_plan_feedback_path(self, mock_ensure):
        """plan_feedback_path 应存入 context。"""
        self.fn({})
        assert self.rt.context.get_ctx("plan_feedback_path") is not None

    def test_raises_when_no_review_text(self):
        """审查意见为空时应抛出 RuntimeError。"""
        self.rt.context.set_ctx("review_text", "")
        with pytest.raises(RuntimeError, match="审查意见为空"):
            self.fn({})


# ── DevGitInit ──

class TestDevGitInit:
    """dev_git_init — Dev 初始化 Git 仓库（Type A）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevGitInit._runtime = self.rt

    def test_calls_dev_agent(self):
        """初始化 Git 调 Dev agent。"""
        DevGitInit.git_init({})
        assert self.mock.call_history[0][0] == "dev"

    def test_sets_dev_conv(self):
        """应创建 dev_conv。"""
        DevGitInit.git_init({})
        assert self.rt.context.get_ctx("dev_conv") is not None

    def test_sets_dev_git_dir(self):
        """dev_git_dir 应为 workspace/Dev 目录。"""
        DevGitInit.git_init({})
        dev_dir = self.rt.context.get_ctx("dev_git_dir")
        assert dev_dir is not None
        assert dev_dir.endswith("Dev")

    def test_returns_git_initted_phase(self):
        """完成后 phase 应为 git_initted。"""
        result = DevGitInit.git_init({})
        assert result["phase"] == "git_initted"


class TestDevGitSummary:
    """dev_git_summary — Dev 写 compact-summary.md（Type A + ensure_write_file）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevGitInit._runtime = self.rt
        self.rt.context.set_ctx("dev_conv", "dev-test")
        self.rt.context.set_ctx("dev_git_dir", str(test_config))

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_dev_agent(self, mock_ensure):
        """写摘要调 Dev agent。"""
        DevGitInit.write_summary({})
        assert self.mock.call_history[0][0] == "dev"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_summary_written_phase(self, mock_ensure):
        """完成后 phase 应为 git_summary_written。"""
        result = DevGitInit.write_summary({})
        assert result["phase"] == "git_summary_written"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_prompt_contains_summary_path(self, mock_ensure):
        """prompt 中包含 compact-summary.md 路径。"""
        DevGitInit.write_summary({})
        prompt = self.mock.call_history[0][2]
        assert "compact-summary.md" in prompt


class TestDevGitFlush:
    """dev_git_flush — 关旧对话 + 开新对话 + 注入上下文（Type D）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevGitInit._runtime = self.rt
        self.rt.context.set_ctx("dev_conv", "dev-old-conv")
        self.rt.context.set_ctx("dev_git_dir", str(test_config))

    def test_closes_old_dev_conv(self):
        """应关闭旧的 dev 对话。"""
        DevGitInit.flush_context({})
        assert any(c[0] == "close:dev" for c in self.mock.call_history)

    def test_creates_new_dev_conv(self):
        """新 dev_conv 应写入 context。"""
        DevGitInit.flush_context({})
        new_conv = self.rt.context.get_ctx("dev_conv")
        assert new_conv is not None
        assert "dev-exec" in new_conv

    def test_calls_dev_agent_with_new_conv(self):
        """调 Dev agent 在新对话中注入上下文。"""
        DevGitInit.flush_context({})
        new_conv = self.rt.context.get_ctx("dev_conv")
        dev_calls = [c for c in self.mock.call_history if c[0] == "dev"]
        assert any(c[1] == new_conv for c in dev_calls)

    def test_returns_git_flushed_phase(self):
        """完成后 phase 应为 git_flushed，judge 为 pass。"""
        result = DevGitInit.flush_context({})
        assert result["phase"] == "git_flushed"
        assert result["judge_result"] == "pass"


# ── DevExecStep ──

class TestDevExecStepWrite:
    """dev_exec_step_letter — Master 写 Step 实现说明信（Type A via write_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevExecStep._runtime = self.rt
        self.rt.context.set_ctx("master_conv", "master-test")
        # 创建 plan.md 含一个 Step
        plan_dir = os.path.join(self.rt.paths.workspace, "Dev")
        os.makedirs(plan_dir, exist_ok=True)
        plan_path = os.path.join(plan_dir, "plan.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write("## Step 1: 实现功能 A\n内容描述")
        self.rt.context.set_ctx("dev_step_index", "0")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_master_agent(self, mock_ensure):
        """调 Master agent 写 Step 说明信。"""
        DevExecStep.write_step_letter({})
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_exec_letter_path(self, mock_ensure):
        """exec_letter_path 应存入 context。"""
        DevExecStep.write_step_letter({})
        assert self.rt.context.get_ctx("exec_letter_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_exec_letter_done_phase(self, mock_ensure):
        """phase 应为 dev_exec_letter_done。"""
        result = DevExecStep.write_step_letter({})
        assert result["phase"] == "dev_exec_letter_done"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_creates_dev_conv_when_not_set(self, mock_ensure):
        """dev_conv 未设时自动创建。"""
        DevExecStep.write_step_letter({})
        assert self.rt.context.get_ctx("dev_conv") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_error_when_no_step_in_plan(self, mock_ensure):
        """plan 中找不到 Step 时返回 error 阶段。"""
        self.rt.context.set_ctx("dev_step_index", "9")
        result = DevExecStep.write_step_letter({})
        assert result["phase"] == "dev_exec_error"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_includes_previous_review_feedback(self, mock_ensure):
        """dev_step_review_feedback 存在时 prompt 应包含。"""
        self.rt.context.set_ctx("dev_step_review_feedback", "上轮审查反馈")
        DevExecStep.write_step_letter({})
        prompt = self.mock.call_history[0][2]
        assert "上轮审查反馈" in prompt

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_includes_escalation_decision(self, mock_ensure):
        """dev_escalation_decision 存在时 prompt 应包含。"""
        self.rt.context.set_ctx("dev_escalation_decision", "人工决策内容")
        DevExecStep.write_step_letter({})
        prompt = self.mock.call_history[0][2]
        assert "人工决策" in prompt


class TestDevExecStepRead:
    """dev_exec_step_read — Dev 读信实现（Type A via read_letter）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevExecStep._runtime = self.rt
        self.rt.context.set_ctx("dev_conv", "dev-test")
        letter = tmp_path / "exec-letter.md"
        letter.write_text("Step 实现说明")
        self.rt.context.set_ctx("exec_letter_path", str(letter))

    def test_calls_dev_agent(self):
        """调 Dev agent 读信实现。"""
        DevExecStep.read_step_letter({})
        assert self.mock.call_history[0][0] == "dev"

    def test_returns_exec_phase(self):
        """phase 应为 dev_exec，judge 为 dev_review_step。"""
        result = DevExecStep.read_step_letter({})
        assert result["phase"] == "dev_exec"
        assert result["judge_result"] == "dev_review_step"


# ── DevReviewStep ──

class TestDevReviewStep:
    """dev_review_step — Reviewer 审查 Step（Type B，多路由路径）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config, tmp_path):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevReviewStep._runtime = self.rt
        # 设置 limits（test_config 缺省故手动设）
        self.rt.limits.fail_rollback_threshold = 3
        self.rt.limits.fail_escalation_threshold = 5
        # 创建 plan.md 含多个 Step
        plan_dir = os.path.join(self.rt.paths.workspace, "Dev")
        os.makedirs(plan_dir, exist_ok=True)
        plan_path = os.path.join(plan_dir, "plan.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write("## Step 1: 实现功能 A\n改动文件：file_a.py\n验收方法：pytest\n"
                     "## Step 2: 实现功能 B\n改动文件：file_b.py\n验收方法：pytest")
        self.rt.context.set_ctx("dev_step_index", "0")
        self.rt.context.set_ctx("dev_total_steps", "2")

    @patch("workflow.phase2.judge_reply", return_value="P")
    def test_passes_when_judge_P_not_last_step(self, mock_judge):
        """judge P 且非最后一步时 phase 为 step_pass。"""
        result = DevReviewStep.run({})
        assert result["phase"] == "step_pass"
        assert result["judge_result"] == "dev_commit"

    @patch("workflow.phase2.judge_reply", return_value="P")
    def test_increments_step_index_when_pass(self, mock_judge):
        """通过后 dev_step_index +1。"""
        DevReviewStep.run({})
        assert self.rt.context.get_ctx("dev_step_index") == "1"

    @patch("workflow.phase2.judge_reply", return_value="P")
    def test_done_when_last_step_passes(self, mock_judge):
        """最后一步通过时 phase 为 dev_exec_done。"""
        self.rt.context.set_ctx("dev_step_index", "1")
        result = DevReviewStep.run({})
        assert result["phase"] == "dev_exec_done"
        assert result["judge_result"] == "dev_commit"

    @patch("workflow.phase2.judge_reply", return_value="F")
    def test_first_fail_returns_retry(self, mock_judge):
        """首次失败返回 step_retry。"""
        result = DevReviewStep.run({})
        assert result["phase"] == "step_fail"
        assert result["judge_result"] == "step_retry"

    @patch("workflow.phase2.judge_reply", return_value="F")
    def test_first_fail_sets_has_failed_flag(self, mock_judge):
        """首次失败设置 dev_step_has_failed=true。"""
        DevReviewStep.run({})
        assert self.rt.context.get_ctx("dev_step_has_failed") == "true"

    @patch("workflow.phase2.judge_reply", return_value="F")
    def test_rollback_when_exceeds_rollback_threshold(self, mock_judge):
        """失败次数达 rollback 阈值时走 step_fail/step_retry（不再回滚）。"""
        self.rt.context.set_ctx("dev_step_has_failed", "true")
        self.rt.context.set_ctx("dev_step_fail_count", "2")
        result = DevReviewStep.run({})
        assert result["phase"] == "step_fail"
        assert result["judge_result"] == "step_retry"

    @patch("workflow.phase2.judge_reply", return_value="F")
    def test_escalate_when_exceeds_escalation_threshold(self, mock_judge):
        """失败次数达 escalate 阈值时走 step_fail/step_retry（不再阻塞）。"""
        self.rt.context.set_ctx("dev_step_has_failed", "true")
        self.rt.context.set_ctx("dev_step_fail_count", "4")
        result = DevReviewStep.run({})
        assert result["phase"] == "step_fail"
        assert result["judge_result"] == "step_retry"

    def test_returns_error_when_no_plan(self):
        """plan.md 不存在时返回 error 阶段。"""
        # 清空 step_index 使其找不到 Step 1
        self.rt.context.set_ctx("dev_step_index", "9")
        result = DevReviewStep.run({})
        assert result["phase"] == "review_step_error"


# ── DevCommit ──

class TestDevCommitGit:
    """dev_commit_git — Dev 提交代码到 Git（Type A），含两条路径。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevCommit._runtime = self.rt
        self.rt.context.set_ctx("dev_conv", "dev-test")

    def test_calls_dev_agent(self):
        """提交调 Dev agent。"""
        DevCommit.git_commit({})
        assert self.mock.call_history[0][0] == "dev"

    def test_returns_done_when_last_step(self):
        """step_index >= total 时返回 done。"""
        self.rt.context.set_ctx("dev_step_index", "2")
        self.rt.context.set_ctx("dev_total_steps", "2")
        result = DevCommit.git_commit({})
        assert result["phase"] == "dev_commit_done"
        assert result["judge_result"] == "done"

    def test_returns_continue_when_more_steps(self):
        """step_index < total 时返回 continue。"""
        self.rt.context.set_ctx("dev_step_index", "0")
        self.rt.context.set_ctx("dev_total_steps", "3")
        result = DevCommit.git_commit({})
        assert result["phase"] == "dev_commit_more"
        assert result["judge_result"] == "continue"

    def test_sets_commit_step_idx_on_continue(self):
        """continue 路径设置 commit_step_idx。"""
        self.rt.context.set_ctx("dev_step_index", "1")
        self.rt.context.set_ctx("dev_total_steps", "3")
        DevCommit.git_commit({})
        assert self.rt.context.get_ctx("commit_step_idx") == "1"


class TestDevCommitSummary:
    """dev_commit_summary — Dev 写进度摘要（Type A + ensure_write_file）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevCommit._runtime = self.rt
        self.rt.context.set_ctx("dev_conv", "dev-test")
        self.rt.context.set_ctx("commit_step_idx", "0")
        self.rt.context.set_ctx("dev_total_steps", "3")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_calls_dev_agent(self, mock_ensure):
        """写摘要调 Dev agent。"""
        DevCommit.write_summary({})
        assert self.mock.call_history[0][0] == "dev"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_returns_summary_done_phase(self, mock_ensure):
        """phase 应为 dev_commit_summary_done。"""
        result = DevCommit.write_summary({})
        assert result["phase"] == "dev_commit_summary_done"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_sets_paths_in_context(self, mock_ensure):
        """commit_summary_path / design_path / plan_path 存入 context。"""
        DevCommit.write_summary({})
        assert self.rt.context.get_ctx("commit_summary_path") is not None
        assert self.rt.context.get_ctx("commit_design_path") is not None
        assert self.rt.context.get_ctx("commit_plan_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_prompt_contains_step_progress(self, mock_ensure):
        """prompt 包含步骤进度。"""
        DevCommit.write_summary({})
        prompt = self.mock.call_history[0][2]
        assert "Step 1/3" in prompt or "0/3" in prompt


class TestDevCommitFlush:
    """dev_commit_flush — 关旧对话 + 开新对话 + 注入 + checkpoint（Type D）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevCommit._runtime = self.rt
        self.rt.context.set_ctx("dev_conv", "dev-old-conv")
        self.rt.context.set_ctx("commit_step_idx", "0")
        self.rt.context.set_ctx("commit_summary_path", "/path/to/summary")
        self.rt.context.set_ctx("commit_design_path", "/path/to/design")
        self.rt.context.set_ctx("commit_plan_path", "/path/to/plan")

    def test_closes_old_dev_conv(self):
        """应关闭旧的 dev 对话。"""
        DevCommit.flush_context({})
        assert any(c[0] == "close:dev" for c in self.mock.call_history)

    def test_creates_new_dev_conv(self):
        """新 dev_conv 应写入 context。"""
        DevCommit.flush_context({})
        new_conv = self.rt.context.get_ctx("dev_conv")
        assert new_conv is not None
        assert "dev-exec" in new_conv

    def test_calls_dev_agent_with_new_conv(self):
        """调 Dev agent 在新对话中注入上下文。"""
        DevCommit.flush_context({})
        new_conv = self.rt.context.get_ctx("dev_conv")
        dev_calls = [c for c in self.mock.call_history if c[0] == "dev"]
        assert any(c[1] == new_conv for c in dev_calls)

    def test_returns_commit_done_phase(self):
        """phase 应为 dev_commit_done，judge 为 dev_exec_step。"""
        result = DevCommit.flush_context({})
        assert result["phase"] == "dev_commit_done"
        assert result["judge_result"] == "dev_exec_step"


class TestDevCommitExit:
    """dev_commit_exit — 空节点。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        state = {"phase": "test"}
        assert DevCommit.exit_pass(state) == state
