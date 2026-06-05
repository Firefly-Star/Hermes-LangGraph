"""Phase 3 集成测试：QA 对齐线性段 + 全路线。"""
from __future__ import annotations
from unittest.mock import patch
import os
import pytest
from agent_runtime import AgentRuntime
from workflow.phase3 import (QA_HANDOFF_DEF, QA_CRITERIA_DEF, QAAlign,
                             QAWriteTestPlan, MASTER_PLAN_REVIEW_DEF,
                             QAWriteTestCase, REVIEWER_CODE_REVIEW_DEF,
                             QARunTests, JudgeTestResult, DevFix)


class TestPhase3QAAlignSegment:
    """QA 对齐线性段：handoff → qa_align → judge 路由。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QAAlign._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")

    def _make_handoff_letter(self):
        """Create a handoff letter file for QA to read."""
        qa_dir = os.path.join(self.rt.paths.workspace, "QA")
        os.makedirs(qa_dir, exist_ok=True)
        lpath = os.path.join(self.rt.paths.handoffs, "master-to-qa-letter.md")
        os.makedirs(os.path.dirname(lpath), exist_ok=True)
        with open(lpath, "w", encoding="utf-8") as f:
            f.write("Master 给 QA 的信：项目测试任务")
        self.rt.context.set_ctx("qaletter_path", lpath)

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_qa_align_reads_handoff(self, mock_ensure):
        """QAAlign.qa 读取 handoff 信件并调 QA agent。"""
        self._make_handoff_letter()
        state = QAAlign.qa({})
        assert state["phase"] == "qa_align_qa_done"
        assert self.rt.context.get_ctx("qa_reply_path") is not None
        assert self.mock.call_history[0][0] == "qa"

    def test_judge_A_exits_alignment(self):
        """QAAlign.judge 返回 A 时对齐完成。"""
        self.rt.context.set_ctx("qa_reply_text", "QA 理解内容")
        self.rt.context.set_ctx("pm_review_text", "PM 审查通过")
        self.rt.context.set_ctx("dev_review_text", "Dev 审查通过")
        self.mock.set_response("你是一个流程裁判", "A")
        state = QAAlign.judge({})
        assert state["judge_result"] == "exit"
        assert state["phase"] == "qa_align_done"
        # Should write understanding.md
        understanding_path = self.rt.context.get_ctx("qa_understanding_path")
        assert understanding_path is not None

    def test_judge_B_routes_back_to_qa(self):
        """QAAlign.judge 返回 B 时路由回 QA。"""
        self.rt.context.set_ctx("qa_reply_text", "QA 理解")
        self.rt.context.set_ctx("pm_review_text", "PM 有反馈需要修改")
        self.rt.context.set_ctx("dev_review_text", "Dev 无反馈")
        self.mock.set_response("你是一个流程裁判", "B")
        # Ensure handoffs dir exists (for writing feedback file)
        os.makedirs(self.rt.paths.handoffs, exist_ok=True)
        state = QAAlign.judge({})
        assert state["judge_result"] == "qa_align_qa"
        assert state["phase"] == "qa_align_feedback"
        # Should write feedback file
        assert self.rt.context.get_ctx("qa_feedback_path") is not None

    def test_judge_C_routes_to_master(self):
        """QAAlign.judge 返回 C 时升级到 Master。"""
        self.rt.context.set_ctx("qa_reply_text", "QA 理解")
        self.rt.context.set_ctx("pm_review_text", "PM 有 ❓需要升级")
        self.rt.context.set_ctx("dev_review_text", "Dev 无反馈")
        self.mock.set_response("你是一个流程裁判", "C")
        state = QAAlign.judge({})
        assert state["judge_result"] == "qa_align_master"
        assert state["phase"] == "qa_align_escalate"

    def test_needs_upgrade_triggers_escalate_even_with_A(self):
        """即使 judge 返回 A，PM/Dev 审查中有 ❓也应升级。"""
        self.rt.context.set_ctx("qa_reply_text", "QA 理解")
        self.rt.context.set_ctx("pm_review_text", "PM ❓需要升级")
        self.rt.context.set_ctx("dev_review_text", "Dev 无反馈")
        self.mock.set_response("你是一个流程裁判", "A")
        state = QAAlign.judge({})
        assert state["judge_result"] == "qa_align_master"
        assert state["phase"] == "qa_align_escalate"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_qa_to_judge_full_sequence(self, mock_ensure):
        """qa_align → judge_A 全串联。"""
        self._make_handoff_letter()
        state = QAAlign.qa({})

        # Manually set review texts (would normally come from PM/Dev nodes)
        self.rt.context.set_ctx("pm_review_text", "PM 审查：QA 理解正确")
        self.rt.context.set_ctx("dev_review_text", "Dev 审查：测试方案可行")

        self.mock.set_response("你是一个流程裁判", "A")
        state = QAAlign.judge(state)
        assert state["judge_result"] == "exit"
        assert state["phase"] == "qa_align_done"


class TestPhase3FullHappyPath:
    """Phase 3 完整 happy path：handoff → align → criteria → plan → code → run → judge。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        QA_HANDOFF_DEF.nodes["qa_handoff"]._runtime = self.rt
        QAAlign._runtime = self.rt
        for node_name in ("qawrite_criteria", "review_qa_criteria", "review_to_qa_artifact"):
            QA_CRITERIA_DEF.nodes[node_name]._runtime = self.rt
        QAWriteTestPlan._runtime = self.rt
        for node_name in ("master_plan_review", "master_plan_review_pass", "master_plan_review_feedback"):
            MASTER_PLAN_REVIEW_DEF.nodes[node_name]._runtime = self.rt
        QAWriteTestCase._runtime = self.rt
        for node_name in ("reviewer_code_review", "reviewer_code_review_pass", "reviewer_code_review_feedback"):
            REVIEWER_CODE_REVIEW_DEF.nodes[node_name]._runtime = self.rt
        QARunTests._runtime = self.rt
        JudgeTestResult._runtime = self.rt
        DevFix._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "/tmp/project_context.md")

        # 创建 QA 工作目录
        qa_dir = os.path.join(self.rt.paths.workspace, "QA")
        os.makedirs(qa_dir, exist_ok=True)

    @patch("workflow.utils.ensure_write_file", return_value=True)
    @patch("workflow.phase3.ensure_write_file", return_value=True)
    def test_handoff_to_judge(self, mock_p3, mock_utils):
        """全线串联：qa_handoff → QAAlign(A) → qa_criteria(P) → plan → master_review(P) → code → reviewer_review(P) → run → judge(A)。"""
        ws = self.rt.paths.workspace

        # ── Node 1: qa_handoff ──
        state = QA_HANDOFF_DEF.nodes["qa_handoff"]({})
        assert "handoff" in state["phase"]
        qaletter_path = self.rt.context.get_ctx("qaletter_path")
        assert qaletter_path is not None

        # ── Node 2: QAAlign.qa ──
        os.makedirs(os.path.dirname(qaletter_path), exist_ok=True)
        with open(qaletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 QA 的信：项目需测试博客系统\nPRD 见 workspace 路径")
        state = QAAlign.qa(state)
        assert state["phase"] == "qa_align_qa_done"
        qa_reply_path = self.rt.context.get_ctx("qa_reply_path")
        assert qa_reply_path is not None

        # ── Node 3: QAAlign.pm ──
        os.makedirs(os.path.dirname(qa_reply_path), exist_ok=True)
        with open(qa_reply_path, "w", encoding="utf-8") as f:
            f.write("QA 理解总结：需要测试博客系统的登录、注册功能，使用 E2E\n")
        state = QAAlign.pm(state)
        assert state["phase"] == "qa_align_pm_done"
        pm_review = self.rt.context.get_ctx("pm_review_text")
        assert pm_review is not None

        # ── Node 4: QAAlign.dev ──
        state = QAAlign.dev(state)
        assert state["phase"] == "qa_align_dev_done"
        dev_review = self.rt.context.get_ctx("dev_review_text")
        assert dev_review is not None

        # ── Node 5: QAAlign.judge → A ──
        self.mock.set_response("你是一个流程裁判。以下是 PM/Dev 的回复。", "A")
        state = QAAlign.judge(state)
        assert state["judge_result"] == "exit"
        assert state["phase"] == "qa_align_done"

        # ── Node 6: QAAlign.judge_exit ──
        state = QAAlign.judge_exit(state)

        # ── Node 7: qawrite_criteria ──
        state = QA_CRITERIA_DEF.nodes["qawrite_criteria"](state)
        assert state["phase"] == "qa_criteria_done"
        criteria_path = self.rt.context.get_ctx("qa_criteria_path")
        assert criteria_path is not None

        # ── Node 8: review_qa_criteria → P ──
        os.makedirs(os.path.dirname(criteria_path), exist_ok=True)
        with open(criteria_path, "w", encoding="utf-8") as f:
            f.write("# QA 审核标准\n- 测试范围覆盖\n- 边界与异常覆盖\n")
        self.mock.set_response("你是一个流程裁判。以下是 Reviewer 的回复。", "P")
        state = QA_CRITERIA_DEF.nodes["review_qa_criteria"](state)
        assert state["judge_result"] == "qa_write_plan"

        # ── Node 9: criteria pass-through ──
        state = QA_CRITERIA_DEF.nodes["review_to_qa_artifact"](state)

        # ── Node 10: QAWriteTestPlan.run ──
        state = QAWriteTestPlan.run(state)
        assert state["phase"] == "qa_plan_written"
        plan_path = self.rt.context.get_ctx("qa_plan_path")
        assert plan_path is not None

        # ── Node 11: master_plan_review → P ──
        self.mock.set_response("你是一个流程裁判。以下是 master 的回复。", "P")
        state = MASTER_PLAN_REVIEW_DEF.nodes["master_plan_review"](state)
        assert state["judge_result"] == "qa_write_code"

        # ── Node 12: master_plan_review pass-through ──
        state = MASTER_PLAN_REVIEW_DEF.nodes["master_plan_review_pass"](state)

        # ── Node 13: QAWriteTestCase.run ──
        state = QAWriteTestCase.run(state)
        assert state["phase"] == "qa_code_written"
        code_path = self.rt.context.get_ctx("qa_code_path")
        assert code_path is not None

        # ── Node 14: reviewer_code_review → P ──
        self.mock.set_response("你是一个流程裁判。以下是 reviewer 的回复。", "P")
        state = REVIEWER_CODE_REVIEW_DEF.nodes["reviewer_code_review"](state)
        assert state["judge_result"] == "qa_run_tests"

        # ── Node 15: reviewer_code_review pass-through ──
        state = REVIEWER_CODE_REVIEW_DEF.nodes["reviewer_code_review_pass"](state)

        # ── Node 16: QARunTests.run ──
        state = QARunTests.run(state)
        assert state["phase"] == "qa_tests_run"
        report_path = self.rt.context.get_ctx("qa_test_report_path")
        assert report_path is not None

        # ── Node 17: JudgeTestResult.judge → A ──
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("全部测试通过，0 failed, 0 error")
        self.mock.set_response("你是一个流程裁判。以下是 QA 的测试报告 的回复。", "A")
        state = JudgeTestResult.judge(state)
        assert state["judge_result"] == "qa_flush"

        # ── Node 18: JudgeTestResult.to_flush ──
        state = JudgeTestResult.to_flush(state)
