"""Checkpoint / ResumeRouter 节点测试。"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import json, os
import pytest
from agent_runtime import AgentRuntime
from workflow.checkpoint import ResumeRouter, save_checkpoint, load_checkpoint, clear_checkpoint


class TestResumeRouter:
    """resume_router — 入口节点，检查 checkpoint + 用户确认（Type D）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ResumeRouter._runtime = self.rt

    def test_returns_pre_flight_when_no_checkpoint(self):
        """无 checkpoint 文件时直接路由 pre_flight。"""
        result = ResumeRouter.router({})
        assert result["phase"] == "pre_flight"

    def test_routes_to_resume_node_when_confirmed(self):
        """有 checkpoint + 用户确认 y 时路由到对应 resume 节点。"""
        save_checkpoint(self.rt, "qa_handoff", "QA 测试")
        self.rt.checkpoint.wait = MagicMock(
            return_value=MagicMock(message="y"))
        result = ResumeRouter.router({})
        assert result["phase"] == "resume_qa_handoff"

    def test_returns_pre_flight_when_user_says_no(self):
        """有 checkpoint + 用户拒绝时路由 pre_flight。"""
        save_checkpoint(self.rt, "qa_handoff", "QA 测试")
        self.rt.checkpoint.wait = MagicMock(
            return_value=MagicMock(message="n"))
        result = ResumeRouter.router({})
        assert result["phase"] == "pre_flight"

    def test_clears_checkpoint_when_user_says_no(self):
        """用户拒绝时应清除 checkpoint 文件。"""
        save_checkpoint(self.rt, "qa_handoff", "QA 测试")
        assert os.path.exists(self.rt.paths.checkpoint)
        self.rt.checkpoint.wait = MagicMock(
            return_value=MagicMock(message="n"))
        ResumeRouter.router({})
        assert not os.path.exists(self.rt.paths.checkpoint)

    def test_ignores_case_and_whitespace_in_user_input(self):
        """用户输入 Y/YES 大小写 + 空白应被正确识别。"""
        save_checkpoint(self.rt, "pm_handoff", "PM 阶段")
        self.rt.checkpoint.wait = MagicMock(
            return_value=MagicMock(message="  YES  "))
        result = ResumeRouter.router({})
        assert result["phase"] == "resume_pm_handoff"

    def test_includes_step_info_when_checkpoint_has_step_idx(self):
        """有 step_idx 的 checkpoint 应传入含步数的提示。"""
        save_checkpoint(self.rt, "dev_exec_step", "Dev 执行", step_idx=3)
        self.rt.checkpoint.wait = MagicMock(
            return_value=MagicMock(message="y"))
        result = ResumeRouter.router({})
        assert result["phase"] == "resume_dev_exec_step"


class TestResumeToPreFlight:
    """resume_to_pre_flight — 空节点。"""

    def test_returns_state_unchanged(self):
        """state 不做修改直接返回。"""
        assert ResumeRouter.to_pre_flight({"phase": "test"}) == {"phase": "test"}


class TestResumePM:
    """resume_pm_handoff — 恢复 PM 阶段（Type D）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ResumeRouter._runtime = self.rt

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_cleans_handoffs_and_pm_outputs(self, mock_open):
        """清理 handoffs 目录和 PM 产出。"""
        handoffs = self.rt.paths.handoffs
        os.makedirs(handoffs, exist_ok=True)
        pm_dir = os.path.join(self.rt.paths.workspace, "PM")
        os.makedirs(pm_dir, exist_ok=True)
        dummy = os.path.join(pm_dir, "PRD.md")
        with open(dummy, "w") as f:
            f.write("test")

        # 先存 checkpoint
        save_checkpoint(self.rt, "pm_handoff", "需求澄清")
        ResumeRouter.resume_pm({})

        assert not os.path.exists(dummy)
        assert not os.path.exists(handoffs)

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_resets_pm_context_keys(self, mock_open):
        """清空 PM 阶段相关的 context key。"""
        for key in ("pm_align_round", "masterletter_path", "pm_reply_path",
                     "pm_reply_text", "pm_conv", "master_reply"):
            self.rt.context.set_ctx(key, "旧值")

        save_checkpoint(self.rt, "pm_handoff", "需求澄清")
        ResumeRouter.resume_pm({})

        for key in ("pm_align_round", "masterletter_path", "pm_reply_path",
                     "pm_reply_text", "pm_conv", "master_reply"):
            assert self.rt.context.get_ctx(key) == ""

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_opens_new_master_conv(self, mock_open):
        """通过 open_master_conv 创建新 Master 对话。"""
        save_checkpoint(self.rt, "pm_handoff", "需求澄清",
                        summary_path="/tmp/summary.md")
        ResumeRouter.resume_pm({})
        mock_open.assert_called_once_with(self.rt, "/tmp/summary.md")

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_returns_pm_handoff_phase(self, mock_open):
        """phase 应为 pm_handoff。"""
        save_checkpoint(self.rt, "pm_handoff", "需求澄清")
        result = ResumeRouter.resume_pm({})
        assert result["phase"] == "pm_handoff"


class TestResumeDev:
    """resume_dev_handoff — 恢复 Dev 阶段（Type D）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ResumeRouter._runtime = self.rt

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_cleans_handoffs_and_dev_outputs(self, mock_open):
        """清理 handoffs 目录和 Dev 产出。"""
        handoffs = self.rt.paths.handoffs
        os.makedirs(handoffs, exist_ok=True)
        dev_dir = os.path.join(self.rt.paths.workspace, "Dev")
        os.makedirs(dev_dir, exist_ok=True)
        dummy = os.path.join(dev_dir, "design.md")
        with open(dummy, "w") as f:
            f.write("test")

        save_checkpoint(self.rt, "dev_handoff", "Dev 实现")
        ResumeRouter.resume_dev({})

        assert not os.path.exists(dummy)
        assert not os.path.exists(handoffs)

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_resets_dev_context_keys(self, mock_open):
        """清空 Dev 阶段相关的 context key。"""
        for key in ("devletter_path", "dev_conv", "dev_reply_path",
                     "design_path", "plan_path", "dev_step_index"):
            self.rt.context.set_ctx(key, "旧值")

        save_checkpoint(self.rt, "dev_handoff", "Dev 实现")
        ResumeRouter.resume_dev({})

        for key in ("devletter_path", "dev_conv", "dev_reply_path",
                     "design_path", "plan_path", "dev_step_index"):
            assert self.rt.context.get_ctx(key) == ""

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_opens_new_master_conv(self, mock_open):
        """通过 open_master_conv 创建新 Master 对话。"""
        save_checkpoint(self.rt, "dev_handoff", "Dev 实现",
                        summary_path="/tmp/summary.md")
        ResumeRouter.resume_dev({})
        mock_open.assert_called_once_with(self.rt, "/tmp/summary.md")

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_returns_dev_handoff_phase(self, mock_open):
        """phase 应为 dev_handoff。"""
        save_checkpoint(self.rt, "dev_handoff", "Dev 实现")
        result = ResumeRouter.resume_dev({})
        assert result["phase"] == "dev_handoff"


class TestResumeQA:
    """resume_qa_handoff — 恢复 QA 阶段（Type D）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ResumeRouter._runtime = self.rt

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_cleans_handoffs_and_qa_outputs(self, mock_open):
        """清理 handoffs 目录和 QA 产出。"""
        handoffs = self.rt.paths.handoffs
        os.makedirs(handoffs, exist_ok=True)
        qa_dir = os.path.join(self.rt.paths.workspace, "QA")
        os.makedirs(qa_dir, exist_ok=True)
        dummy = os.path.join(qa_dir, "test-plan.md")
        with open(dummy, "w") as f:
            f.write("test")

        save_checkpoint(self.rt, "qa_handoff", "QA 测试")
        ResumeRouter.resume_qa({})

        assert not os.path.exists(dummy)
        assert not os.path.exists(handoffs)

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_resets_qa_context_keys(self, mock_open):
        """清空 QA 阶段相关的 context key。"""
        for key in ("qaletter_path", "qa_feedback_path", "qa_understanding_path",
                     "qa_plan_path", "qa_plan_feedback_path", "qa_plan_review",
                     "qa_code_path", "qa_code_feedback_path", "qa_code_review",
                     "qa_test_report_path", "qa_bug_report_path", "qa_conv"):
            self.rt.context.set_ctx(key, "旧值")

        save_checkpoint(self.rt, "qa_handoff", "QA 测试")
        ResumeRouter.resume_qa({})

        for key in ("qaletter_path", "qa_feedback_path", "qa_understanding_path",
                     "qa_plan_path", "qa_plan_feedback_path", "qa_plan_review",
                     "qa_code_path", "qa_code_feedback_path", "qa_code_review",
                     "qa_test_report_path", "qa_bug_report_path", "qa_conv"):
            assert self.rt.context.get_ctx(key) == ""

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_cleans_pm_and_dev_conv_too(self, mock_open):
        """同时清理 pm_conv 和 dev_conv，避免 QA 拿到旧对话。"""
        self.rt.context.set_ctx("pm_conv", "old-pm")
        self.rt.context.set_ctx("dev_conv", "old-dev")

        save_checkpoint(self.rt, "qa_handoff", "QA 测试")
        ResumeRouter.resume_qa({})

        assert self.rt.context.get_ctx("pm_conv") == ""
        assert self.rt.context.get_ctx("dev_conv") == ""

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_opens_new_master_conv(self, mock_open):
        """通过 open_master_conv 创建新 Master 对话。"""
        save_checkpoint(self.rt, "qa_handoff", "QA 测试",
                        summary_path="/tmp/summary.md")
        ResumeRouter.resume_qa({})
        mock_open.assert_called_once_with(self.rt, "/tmp/summary.md")

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_returns_qa_handoff_phase(self, mock_open):
        """phase 应为 qa_handoff。"""
        save_checkpoint(self.rt, "qa_handoff", "QA 测试")
        result = ResumeRouter.resume_qa({})
        assert result["phase"] == "qa_handoff"


class TestResumeDevExec:
    """resume_dev_exec_step — 恢复 Dev 执行（Type D）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ResumeRouter._runtime = self.rt
        self.rt.context.set_bg("dev_principles", "Dev 开发原则")

    def test_cleans_handoffs(self):
        """清理 handoffs 目录。"""
        handoffs = self.rt.paths.handoffs
        os.makedirs(handoffs, exist_ok=True)
        dummy = os.path.join(handoffs, "letter.md")
        with open(dummy, "w") as f:
            f.write("test")

        save_checkpoint(self.rt, "dev_exec_step", "Dev 执行", step_idx=2)
        ResumeRouter.resume_dev_exec({})

        assert not os.path.exists(handoffs)

    def test_restores_dev_conv(self):
        """调用 _restore_dev_conv，创建新的 dev 执行对话。"""
        save_checkpoint(self.rt, "dev_exec_step", "Dev 执行", step_idx=2)
        ResumeRouter.resume_dev_exec({})
        # _restore_dev_conv 中调用了 call_agent，记录在 call_history
        assert len(self.mock.call_history) > 0
        assert self.mock.call_history[0][0] == "dev"

    def test_sets_dev_step_index_in_context(self):
        """从 checkpoint 恢复 step_idx 到 context。"""
        save_checkpoint(self.rt, "dev_exec_step", "Dev 执行", step_idx=3)
        ResumeRouter.resume_dev_exec({})
        assert self.rt.context.get_ctx("dev_step_index") == "3"

    def test_resets_fail_count_and_review_state(self):
        """重置 fail_count、has_failed、review_feedback 等步进状态。"""
        self.rt.context.set_ctx("dev_step_fail_count", "99")
        self.rt.context.set_ctx("dev_step_has_failed", "true")
        save_checkpoint(self.rt, "dev_exec_step", "Dev 执行", step_idx=0)
        ResumeRouter.resume_dev_exec({})
        assert self.rt.context.get_ctx("dev_step_fail_count") == "0"
        assert self.rt.context.get_ctx("dev_step_has_failed") == "false"

    def test_returns_dev_exec_step_phase(self):
        """phase 应为 dev_exec_step。"""
        save_checkpoint(self.rt, "dev_exec_step", "Dev 执行", step_idx=0)
        result = ResumeRouter.resume_dev_exec({})
        assert result["phase"] == "dev_exec_step"


class TestResumePhase4:
    """resume_phase4_handoff — 恢复交付阶段（Type D）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        ResumeRouter._runtime = self.rt

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_cleans_handoffs(self, mock_open):
        """清理 handoffs 目录。"""
        handoffs = self.rt.paths.handoffs
        os.makedirs(handoffs, exist_ok=True)
        dummy = os.path.join(handoffs, "letter.md")
        with open(dummy, "w") as f:
            f.write("test")

        save_checkpoint(self.rt, "consistency_audit", "交付")
        ResumeRouter.resume_phase4({})

        assert not os.path.exists(handoffs)

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_opens_new_master_conv(self, mock_open):
        """通过 open_master_conv 创建新 Master 对话。"""
        save_checkpoint(self.rt, "consistency_audit", "交付",
                        summary_path="/tmp/summary.md")
        ResumeRouter.resume_phase4({})
        mock_open.assert_called_once_with(self.rt, "/tmp/summary.md")

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_clears_checkpoint(self, mock_open):
        """Phase 4 恢复后清除 checkpoint（无需断点保护）。"""
        save_checkpoint(self.rt, "consistency_audit", "交付")
        assert os.path.exists(self.rt.paths.checkpoint)
        ResumeRouter.resume_phase4({})
        assert not os.path.exists(self.rt.paths.checkpoint)

    @patch("workflow.checkpoint.open_master_conv", return_value="new-master-conv")
    def test_returns_consistency_audit_phase(self, mock_open):
        """phase 应为 consistency_audit。"""
        save_checkpoint(self.rt, "consistency_audit", "交付")
        result = ResumeRouter.resume_phase4({})
        assert result["phase"] == "consistency_audit"


class TestCheckpointUtils:
    """save_checkpoint / load_checkpoint / clear_checkpoint 工具函数。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)

    def test_save_and_load_checkpoint(self):
        """保存后可读回相同的 resume_node 和 phase_name。"""
        save_checkpoint(self.rt, "pm_handoff", "需求澄清", step_idx=2,
                        summary_path="/tmp/summary.md")
        cp = load_checkpoint(self.rt)
        assert cp is not None
        assert cp["resume_node"] == "pm_handoff"
        assert cp["phase_name"] == "需求澄清"
        assert cp["step_idx"] == 2
        assert cp["summary_path"] == "/tmp/summary.md"

    def test_load_returns_none_when_no_file(self):
        """无 checkpoint 文件时返回 None。"""
        assert load_checkpoint(self.rt) is None

    def test_clear_checkpoint_removes_file(self):
        """clear_checkpoint 删除 checkpoint 文件。"""
        save_checkpoint(self.rt, "dev_handoff", "Dev 实现")
        assert os.path.exists(self.rt.paths.checkpoint)
        clear_checkpoint(self.rt)
        assert not os.path.exists(self.rt.paths.checkpoint)

    def test_clear_checkpoint_safe_when_no_file(self):
        """checkpoint 文件不存在时 clear_checkpoint 不抛异常。"""
        clear_checkpoint(self.rt)  # should not raise
