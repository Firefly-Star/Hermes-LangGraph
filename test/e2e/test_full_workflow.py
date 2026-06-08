"""E2E 全链路测试：Phase 0 → 1 → 2 → 3 → 4。"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import os
import pytest
from agent_runtime import AgentRuntime
from langgraph.graph import END
from workflow.phase0 import PreFlightClarify
from workflow.phase1 import (PM_HANDOFF_DEF, PM_CRITERIA_DEF, PMAlign,
    MasterReplyPM, JudgeMasterReply, PMWriteDoc, ReviewPMOutput, HumanReview)
from workflow.phase2 import (DEV_HANDOFF_DEF, DEV_CRITERIA_DEF, DevAlign,
    DevWriteDesign, DEV_DESIGN_REVIEW_DEF, WriteDesignSummary,
    DevWritePlan, DEV_PLAN_REVIEW_DEF,
    DevGitInit, DevExecStep, DevReviewStep, DevCommit)
from workflow.phase3 import (QA_HANDOFF_DEF, QA_CRITERIA_DEF, QAAlign,
    QAWriteTestPlan, MASTER_PLAN_REVIEW_DEF, QAWriteTestCase,
    REVIEWER_CODE_REVIEW_DEF, QARunTests, JudgeTestResult)
from workflow.phase4 import ConsistencyAudit, WriteMaintenanceDocs, DeliverySummary
from workflow.flush import (MASTER_FLUSH_CLARIFY_DEF, MASTER_FLUSH_PM_DEF,
                             MASTER_FLUSH_DEV_DEF, MASTER_FLUSH_QA_DEF)


class TestE2EFullWorkflow:
    """Phase 0 → 1 → 2 → 3 → 4 全链路测试。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)

        # ── Phase 0 ──
        PreFlightClarify._runtime = self.rt

        # ── Phase 1 ──
        PM_HANDOFF_DEF.nodes["pm_handoff"]._runtime = self.rt
        PMAlign._runtime = self.rt
        MasterReplyPM._runtime = self.rt
        JudgeMasterReply._runtime = self.rt
        for n in ("pmwrite_criteria", "review_pm_criteria", "review_to_pm_artifact"):
            PM_CRITERIA_DEF.nodes[n]._runtime = self.rt
        PMWriteDoc._runtime = self.rt
        ReviewPMOutput._runtime = self.rt
        HumanReview._runtime = self.rt

        # ── Phase 2 ──
        DEV_HANDOFF_DEF.nodes["dev_handoff"]._runtime = self.rt
        DevAlign._runtime = self.rt
        for n in ("devwrite_criteria", "review_dev_criteria", "review_to_dev_artifact"):
            DEV_CRITERIA_DEF.nodes[n]._runtime = self.rt
        DevWriteDesign._runtime = self.rt
        for n in ("dev_design_review", "dev_design_review_pass"):
            DEV_DESIGN_REVIEW_DEF.nodes[n]._runtime = self.rt
        WriteDesignSummary._runtime = self.rt
        DevWritePlan._runtime = self.rt
        for n in ("dev_plan_review", "dev_plan_review_pass"):
            DEV_PLAN_REVIEW_DEF.nodes[n]._runtime = self.rt
        DevGitInit._runtime = self.rt
        DevExecStep._runtime = self.rt
        DevReviewStep._runtime = self.rt
        DevCommit._runtime = self.rt

        # ── Phase 3 ──
        QA_HANDOFF_DEF.nodes["qa_handoff"]._runtime = self.rt
        QAAlign._runtime = self.rt
        for n in ("qawrite_criteria", "review_qa_criteria", "review_to_qa_artifact"):
            QA_CRITERIA_DEF.nodes[n]._runtime = self.rt
        QAWriteTestPlan._runtime = self.rt
        for n in ("master_plan_review", "master_plan_review_pass", "master_plan_review_feedback"):
            MASTER_PLAN_REVIEW_DEF.nodes[n]._runtime = self.rt
        QAWriteTestCase._runtime = self.rt
        for n in ("reviewer_code_review", "reviewer_code_review_pass", "reviewer_code_review_feedback"):
            REVIEWER_CODE_REVIEW_DEF.nodes[n]._runtime = self.rt
        QARunTests._runtime = self.rt
        JudgeTestResult._runtime = self.rt

        # ── Master flush 节点 ──
        for domain, defn in [("clarify", MASTER_FLUSH_CLARIFY_DEF),
                              ("pm", MASTER_FLUSH_PM_DEF),
                              ("dev", MASTER_FLUSH_DEV_DEF),
                              ("qa", MASTER_FLUSH_QA_DEF)]:
            for n in (f"master_flush_{domain}_summary", f"master_flush_{domain}_conv"):
                defn.nodes[n]._runtime = self.rt

        # ── Phase 4 ──
        ConsistencyAudit._runtime = self.rt
        WriteMaintenanceDocs._runtime = self.rt
        DeliverySummary._runtime = self.rt

        # 全局 bg context
        self.rt.context.set_bg("project_context_path", "/tmp/project_context.md")

        # Judge mock 预设（最长前缀匹配，All happy path）
        self._setup_judge_mocks()

    def _setup_judge_mocks(self):
        """预配置所有 judge_reply 的 mock 返回值。"""
        self.mock.set_response(
            "你是一个流程裁判。以下是 Master 的回复。", "A")
        self.mock.set_response(
            "你是一个流程裁判。以下是 Reviewer 的回复。", "P")
        self.mock.set_response(
            "你是一个流程裁判。以下是 reviewer 的回复。", "P")
        self.mock.set_response(
            "你是一个流程裁判。以下是 master 的回复。", "P")
        self.mock.set_response(
            "你是一个流程裁判。以下是 PM 的回复。", "P")
        self.mock.set_response(
            "你是一个流程裁判。以下是 PM/Dev 的回复。", "A")
        self.mock.set_response(
            "你是一个流程裁判。以下是 QA 的测试报告 的回复。", "A")

    # ── 5 个 patch ──────────────────────────────────────────
    @patch("workflow.phase0.clarify_loop", return_value="用户确认完成")
    @patch("workflow.utils.ensure_write_file", return_value=True)
    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    @patch("workflow.phase2.ensure_write_file", return_value=True)
    @patch("workflow.phase3.ensure_write_file", return_value=True)
    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_full_workflow(
        self, mock_p4, mock_p3, mock_p2, mock_flush, mock_utils, mock_clarify
    ):
        """全链路：Phase 0 → 1 → 2 → 3 → 4，验证串联正确性。"""
        ws = self.rt.paths.workspace

        # ================================================================
        # Phase 0: 需求澄清 (3 nodes)
        # ================================================================
        state = PreFlightClarify.init({})
        assert state["phase"] == "clarify_inject"
        assert self.rt.context.get_ctx("master_conv") is not None

        state = PreFlightClarify.clarify(state)
        assert state["phase"] == "clarify_close"
        assert self.rt.context.get_ctx("clarify_reason") == "用户确认完成"

        state = PreFlightClarify.close(state)
        assert state["phase"] == "done"

        # ================================================================
        # Flush 澄清 → PM (2 nodes)
        # ================================================================
        state = MASTER_FLUSH_CLARIFY_DEF.nodes["master_flush_clarify_summary"](state)
        assert "flushed" in state["phase"]
        assert self.rt.context.get_ctx("phase_summary_path") is not None

        state = MASTER_FLUSH_CLARIFY_DEF.nodes["master_flush_clarify_conv"](state)
        assert "conv_flushed" in state["phase"]
        # checkpoint 写入验证（conv_name 秒级精度，不比较 conv 名）
        assert os.path.exists(self.rt.paths.checkpoint)

        # ================================================================
        # Phase 1: PM 出方案 (simplified)
        # ================================================================
        # ── handoff ──
        state = PM_HANDOFF_DEF.nodes["pm_handoff"](state)
        assert "handoff" in state["phase"]
        pmletter_path = self.rt.context.get_ctx("pmletter_path")
        assert pmletter_path is not None

        # ── PMAlign.read ──
        os.makedirs(os.path.dirname(pmletter_path), exist_ok=True)
        with open(pmletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 PM 的信：需开发博客系统")
        state = PMAlign.read(state)
        assert state["phase"] == "pm_align_done"
        pm_reply_path = self.rt.context.get_ctx("pm_reply_path")
        assert pm_reply_path is not None

        # ── MasterReplyPM.run ──
        os.makedirs(os.path.dirname(pm_reply_path), exist_ok=True)
        with open(pm_reply_path, "w", encoding="utf-8") as f:
            f.write("PM 回信：理解了需求")
        state = MasterReplyPM.run(state)
        assert state["phase"] == "master_reply_done"
        assert self.rt.context.get_ctx("master_reply") is not None

        # ── JudgeMasterReply.run → A ──
        state = JudgeMasterReply.run(state)
        assert state["judge_result"] == "A"

        # ── pm_criteria write ──
        state = PM_CRITERIA_DEF.nodes["pmwrite_criteria"](state)
        assert state["phase"] == "pm_criteria_done"
        pm_criteria_path = self.rt.context.get_ctx("pm_criteria_path")
        assert pm_criteria_path is not None

        # ── pm_criteria review → P ──
        os.makedirs(os.path.dirname(pm_criteria_path), exist_ok=True)
        with open(pm_criteria_path, "w", encoding="utf-8") as f:
            f.write("# PM 审核标准\n- 需求完整性\n- MVP 边界")
        state = PM_CRITERIA_DEF.nodes["review_pm_criteria"](state)
        assert state["judge_result"] == "pm_write_doc"

        # ── criteria pass-through ──
        state = PM_CRITERIA_DEF.nodes["review_to_pm_artifact"](state)

        # ── PMWriteDoc: write_prd_letter → read_prd_letter ──
        state = PMWriteDoc.write_prd_letter(state)
        assert state["phase"] == "pm_read_prd"
        prdletter_path = self.rt.context.get_ctx("prdletter_path")
        assert prdletter_path is not None

        os.makedirs(os.path.dirname(prdletter_path), exist_ok=True)
        with open(prdletter_path, "w", encoding="utf-8") as f:
            f.write("Master 要求 PM 编写 PRD")
        state = PMWriteDoc.read_prd_letter(state)
        assert state["phase"] == "pm_write_proto_letter"

        # ── PMWriteDoc: write_proto_letter → read_proto_letter ──
        state = PMWriteDoc.write_proto_letter(state)
        assert state["phase"] == "pm_read_proto"
        protoletter_path = self.rt.context.get_ctx("protoletter_path")
        assert protoletter_path is not None

        os.makedirs(os.path.dirname(protoletter_path), exist_ok=True)
        with open(protoletter_path, "w", encoding="utf-8") as f:
            f.write("Master 要求 PM 编写原型")
        state = PMWriteDoc.read_proto_letter(state)
        assert state["phase"] == "done"

        # ── ReviewPMOutput.run → P ──
        pm_dir = os.path.join(ws, "PM")
        os.makedirs(pm_dir, exist_ok=True)
        with open(os.path.join(pm_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write("# PRD\n博客系统需求文档")
        with open(os.path.join(pm_dir, "prototype.html"), "w", encoding="utf-8") as f:
            f.write("<html><body>原型</body></html>")
        os.makedirs(os.path.join(ws, "reviewer", "pm"), exist_ok=True)

        state = ReviewPMOutput.run(state)
        assert state["judge_result"] == "human_review"
        assert state["phase"] == "review_done"

        # ── HumanReview.run → EOF (pass) ──
        self.rt.checkpoint.wait = MagicMock(return_value=MagicMock(message=""))
        state = HumanReview.run(state)
        assert state["judge_result"] == END
        assert state["phase"] == "done"

        # ================================================================
        # Flush PM → Dev (2 nodes)
        # ================================================================
        state = MASTER_FLUSH_PM_DEF.nodes["master_flush_pm_summary"](state)
        assert "flushed" in state["phase"]

        state = MASTER_FLUSH_PM_DEF.nodes["master_flush_pm_conv"](state)
        assert "conv_flushed" in state["phase"]
        assert os.path.exists(self.rt.paths.checkpoint)

        # ================================================================
        # Phase 2: Dev 实现
        # ================================================================
        # ── dev_handoff ──
        state = DEV_HANDOFF_DEF.nodes["dev_handoff"](state)
        assert "handoff" in state["phase"]
        devletter_path = self.rt.context.get_ctx("devletter_path")
        assert devletter_path is not None

        # ── DevAlign.dev ──
        os.makedirs(os.path.dirname(devletter_path), exist_ok=True)
        with open(devletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 Dev 的信：开发博客系统")
        state = DevAlign.dev(state)
        assert state["phase"] == "dev_align_dev_done"
        dev_reply_path = self.rt.context.get_ctx("dev_reply_path")
        assert dev_reply_path is not None

        # ── DevAlign.pm ──
        os.makedirs(os.path.dirname(dev_reply_path), exist_ok=True)
        with open(dev_reply_path, "w", encoding="utf-8") as f:
            f.write("Dev 理解总结：需要开发博客系统")
        state = DevAlign.pm(state)
        assert state["phase"] == "dev_align_pm_done"

        # ── DevAlign.judge → exit ──
        state = DevAlign.judge(state)
        assert state["judge_result"] == "exit"
        assert state["phase"] == "dev_align_done"

        # ── DevAlign.judge_exit (pass-through) ──
        state = DevAlign.judge_exit(state)

        # ── dev_criteria write ──
        state = DEV_CRITERIA_DEF.nodes["devwrite_criteria"](state)
        assert state["phase"] == "dev_criteria_done"
        dev_criteria_path = self.rt.context.get_ctx("dev_criteria_path")
        assert dev_criteria_path is not None

        # ── dev_criteria review → P ──
        os.makedirs(os.path.dirname(dev_criteria_path), exist_ok=True)
        with open(dev_criteria_path, "w", encoding="utf-8") as f:
            f.write("# Dev 设计审核标准\n- 架构合理性\n- 功能完整性")
        state = DEV_CRITERIA_DEF.nodes["review_dev_criteria"](state)
        assert state["judge_result"] == "dev_write_design"

        # ── criteria pass-through ──
        state = DEV_CRITERIA_DEF.nodes["review_to_dev_artifact"](state)

        # ── DevWriteDesign ──
        state = DevWriteDesign.write_design_letter(state)
        assert state["phase"] == "dev_design_letter_done"
        designletter_path = self.rt.context.get_ctx("designletter_path")
        assert designletter_path is not None

        os.makedirs(os.path.dirname(designletter_path), exist_ok=True)
        with open(designletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 Dev：请写详细设计")
        state = DevWriteDesign.read_design_letter(state)
        assert state["phase"] == "dev_design_done"

        # ── dev_design_review → P ──
        state = DEV_DESIGN_REVIEW_DEF.nodes["dev_design_review"](state)
        assert state["judge_result"] == "dev_write_plan"

        # ── design review pass-through ──
        state = DEV_DESIGN_REVIEW_DEF.nodes["dev_design_review_pass"](state)

        # ── WriteDesignSummary ──
        state = WriteDesignSummary.run(state)
        assert state["phase"] == "design_summary_done"

        # ── DevWritePlan ──
        state = DevWritePlan.write_plan_letter(state)
        assert state["phase"] == "dev_plan_letter_done"
        planletter_path = self.rt.context.get_ctx("planletter_path")
        assert planletter_path is not None

        os.makedirs(os.path.dirname(planletter_path), exist_ok=True)
        with open(planletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 Dev：请写实现计划")
        state = DevWritePlan.read_plan_letter(state)
        assert state["phase"] == "dev_plan_done"

        # ── dev_plan_review → P ──
        # 创建 plan.md 供 count_steps 读取
        dev_dir = os.path.join(ws, "Dev")
        os.makedirs(dev_dir, exist_ok=True)
        with open(os.path.join(dev_dir, "plan.md"), "w", encoding="utf-8") as f:
            f.write("## Step 1\n### 验收方法\necho ok\n")
        with open(os.path.join(dev_dir, "design.md"), "w", encoding="utf-8") as f:
            f.write("# Design\n系统架构设计\n")

        state = DEV_PLAN_REVIEW_DEF.nodes["dev_plan_review"](state)
        assert state["judge_result"] == "dev_exec"
        assert self.rt.context.get_ctx("dev_step_index") == "0"
        assert int(self.rt.context.get_ctx("dev_total_steps") or "0") >= 1

        # ── plan review pass-through ──
        state = DEV_PLAN_REVIEW_DEF.nodes["dev_plan_review_pass"](state)

        # ── DevExecStep ──
        state = DevExecStep.write_step_letter(state)
        assert state["phase"] == "dev_exec_letter_done"
        exec_letter_path = self.rt.context.get_ctx("exec_letter_path")
        assert exec_letter_path is not None

        os.makedirs(os.path.dirname(exec_letter_path), exist_ok=True)
        with open(exec_letter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 Dev：请实现 Step 1")
        state = DevExecStep.read_step_letter(state)
        assert state["phase"] == "dev_exec"

        # ── DevReviewStep.run → P ──
        state = DevReviewStep.run(state)
        assert state["judge_result"] == "dev_commit"

        # ── DevCommit.git_commit → done ──
        state = DevCommit.git_commit(state)
        assert state["judge_result"] == "done"
        assert state["phase"] == "dev_commit_done"

        # ================================================================
        # Flush Dev → QA (2 nodes)
        # ================================================================
        state = MASTER_FLUSH_DEV_DEF.nodes["master_flush_dev_summary"](state)
        assert "flushed" in state["phase"]

        state = MASTER_FLUSH_DEV_DEF.nodes["master_flush_dev_conv"](state)
        assert "conv_flushed" in state["phase"]

        # ================================================================
        # Phase 3: QA 测试 (simplified)
        # ================================================================
        # ── qa_handoff ──
        state = QA_HANDOFF_DEF.nodes["qa_handoff"](state)
        assert "handoff" in state["phase"]
        qaletter_path = self.rt.context.get_ctx("qaletter_path")
        assert qaletter_path is not None

        # ── QAAlign.qa ──
        os.makedirs(os.path.dirname(qaletter_path), exist_ok=True)
        with open(qaletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 QA 的信：项目测试任务")
        state = QAAlign.qa(state)
        assert state["phase"] == "qa_align_qa_done"
        qa_reply_path = self.rt.context.get_ctx("qa_reply_path")
        assert qa_reply_path is not None

        # ── QAAlign.pm ──
        os.makedirs(os.path.dirname(qa_reply_path), exist_ok=True)
        with open(qa_reply_path, "w", encoding="utf-8") as f:
            f.write("QA 理解总结：需要测试博客系统")
        state = QAAlign.pm(state)
        assert state["phase"] == "qa_align_pm_done"

        # ── QAAlign.dev ──
        state = QAAlign.dev(state)
        assert state["phase"] == "qa_align_dev_done"

        # ── QAAlign.judge → A ──
        state = QAAlign.judge(state)
        assert state["judge_result"] == "exit"

        # ── QAAlign.judge_exit (pass-through) ──
        state = QAAlign.judge_exit(state)

        # ── qa_criteria write ──
        state = QA_CRITERIA_DEF.nodes["qawrite_criteria"](state)
        assert state["phase"] == "qa_criteria_done"
        qa_criteria_path = self.rt.context.get_ctx("qa_criteria_path")
        assert qa_criteria_path is not None

        # ── qa_criteria review → P ──
        os.makedirs(os.path.dirname(qa_criteria_path), exist_ok=True)
        with open(qa_criteria_path, "w", encoding="utf-8") as f:
            f.write("# QA 审核标准\n- 测试范围覆盖\n")
        state = QA_CRITERIA_DEF.nodes["review_qa_criteria"](state)
        assert state["judge_result"] == "qa_write_plan"

        # ── criteria pass-through ──
        state = QA_CRITERIA_DEF.nodes["review_to_qa_artifact"](state)

        # ── QAWriteTestPlan.run ──
        state = QAWriteTestPlan.run(state)
        assert state["phase"] == "qa_plan_written"
        qa_plan_path = self.rt.context.get_ctx("qa_plan_path")
        assert qa_plan_path is not None

        # ── master_plan_review → P ──
        os.makedirs(os.path.dirname(qa_plan_path), exist_ok=True)
        with open(qa_plan_path, "w", encoding="utf-8") as f:
            f.write("# 测试计划\nE2E 测试方案")
        state = MASTER_PLAN_REVIEW_DEF.nodes["master_plan_review"](state)
        assert state["judge_result"] == "qa_write_code"

        # ── plan review pass-through ──
        state = MASTER_PLAN_REVIEW_DEF.nodes["master_plan_review_pass"](state)

        # ── QAWriteTestCase.run ──
        state = QAWriteTestCase.run(state)
        assert state["phase"] == "qa_code_written"
        qa_code_path = self.rt.context.get_ctx("qa_code_path")
        assert qa_code_path is not None

        # ── reviewer_code_review → P ──
        # qa_code_path 是 tests 目录，reviewer 只需要路径引用
        state = REVIEWER_CODE_REVIEW_DEF.nodes["reviewer_code_review"](state)
        assert state["judge_result"] == "qa_run_tests"

        # ── code review pass-through ──
        state = REVIEWER_CODE_REVIEW_DEF.nodes["reviewer_code_review_pass"](state)

        # ── QARunTests.run ──
        state = QARunTests.run(state)
        assert state["phase"] == "qa_tests_run"
        qa_report_path = self.rt.context.get_ctx("qa_test_report_path")
        assert qa_report_path is not None

        # ── JudgeTestResult.judge → to_flush ──
        os.makedirs(os.path.dirname(qa_report_path), exist_ok=True)
        with open(qa_report_path, "w", encoding="utf-8") as f:
            f.write("全部测试通过，0 failed, 0 error")
        state = JudgeTestResult.judge(state)
        assert state["judge_result"] == "qa_flush"

        # ── JudgeTestResult.to_flush (pass-through) ──
        state = JudgeTestResult.to_flush(state)

        # ================================================================
        # Flush QA → Phase 4 (2 nodes)
        # ================================================================
        state = MASTER_FLUSH_QA_DEF.nodes["master_flush_qa_summary"](state)
        assert "flushed" in state["phase"]

        state = MASTER_FLUSH_QA_DEF.nodes["master_flush_qa_conv"](state)
        assert "conv_flushed" in state["phase"]

        # ================================================================
        # Phase 4: 交付 (3 nodes)
        # ================================================================
        # ── ConsistencyAudit.run ──
        state = ConsistencyAudit.run(state)
        assert state["phase"] == "audit_done"
        assert self.rt.context.get_ctx("audit_path") is not None

        # ── WriteMaintenanceDocs.run ──
        state = WriteMaintenanceDocs.run(state)
        assert state["phase"] == "docs_written"
        assert self.rt.context.get_ctx("readme_path") is not None
        assert self.rt.context.get_ctx("deploy_path") is not None

        # ── DeliverySummary.run → END ──
        state = DeliverySummary.run(state)
        assert state["phase"] == "delivery_done"
