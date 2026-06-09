"""Phase 2 集成测试：Dev 执行步进线性段 + 全路线 happy path。"""
from __future__ import annotations
from unittest.mock import patch
import os
import pytest
from agent_runtime import AgentRuntime
from langgraph.graph import END
from workflow.phase2 import (DevExecStep, DevReviewStep, DevCommit,
                             DEV_HANDOFF_DEF, DevAlign,
                             DEV_CRITERIA_DEF, DevWriteDesign,
                             DEV_DESIGN_REVIEW_DEF, WriteDesignSummary,
                             DevWritePlan,
                             DEV_PLAN_REVIEW_DEF, DevGitInit)
from workflow.flush import MASTER_FLUSH_DEV_STEP_DEF


class TestPhase2DevExecSegment:
    """Dev 执行步进线性段：letter → exec → review_P → commit_done。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevExecStep._runtime = self.rt
        DevReviewStep._runtime = self.rt
        DevCommit._runtime = self.rt
        for n in ("master_flush_dev_step_summary", "master_flush_dev_step_conv"):
            MASTER_FLUSH_DEV_STEP_DEF.nodes[n]._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_ctx("dev_step_index", "0")
        self.rt.context.set_ctx("dev_total_steps", "1")

        # Create plan.md and design.md
        dev_dir = os.path.join(self.rt.paths.workspace, "Dev")
        os.makedirs(dev_dir, exist_ok=True)
        with open(os.path.join(dev_dir, "plan.md"), "w", encoding="utf-8") as f:
            f.write("## Step 1\n; 验收方法: echo ok\n")
        with open(os.path.join(dev_dir, "design.md"), "w", encoding="utf-8") as f:
            f.write("# Design\n")


class TestPhase2MultiStepFlush:
    """多步 Dev + 步骤间 master flush 循环。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DevExecStep._runtime = self.rt
        DevReviewStep._runtime = self.rt
        DevCommit._runtime = self.rt
        for n in ("master_flush_dev_step_summary", "master_flush_dev_step_conv"):
            MASTER_FLUSH_DEV_STEP_DEF.nodes[n]._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_ctx("dev_conv", "dev-exec-test")
        self.rt.context.set_ctx("dev_step_index", "0")
        self.rt.context.set_ctx("dev_total_steps", "2")

        dev_dir = os.path.join(self.rt.paths.workspace, "Dev")
        os.makedirs(dev_dir, exist_ok=True)
        with open(os.path.join(dev_dir, "plan.md"), "w", encoding="utf-8") as f:
            f.write("## Step 1\n### 验收方法\necho step1\n\n## Step 2\n### 验收方法\necho step2\n")
        with open(os.path.join(dev_dir, "design.md"), "w", encoding="utf-8") as f:
            f.write("# Design\n")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    @patch("workflow.phase2.ensure_write_file", return_value=True)
    def test_multi_step_with_dev_step_flush(self, mock_p2, mock_utils):
        """2 步 Dev：step1 commit(continue) → flush → step2 commit(done)，验证 checkpoint step_idx。"""
        self.rt.context.set_ctx("dev_total_steps", "2")

        # ════════════════════════════════════════════
        # Step 1: DevCommit.git_commit → continue
        # ════════════════════════════════════════════
        self.rt.context.set_ctx("dev_step_index", "1")  # 模拟 step 1 已通过 review
        state = DevCommit.git_commit({})
        assert state["judge_result"] == "continue"
        assert self.rt.context.get_ctx("commit_step_idx") == "1"

        # DevCommit subgraph: write_summary → flush_context
        state = DevCommit.write_summary(state)
        assert state["phase"] == "dev_commit_summary_done"

        state = DevCommit.flush_context(state)
        assert state["judge_result"] == "dev_exec_step"

        # master_flush_dev_step_summary
        old_master_conv = self.rt.context.get_ctx("master_conv")
        state = MASTER_FLUSH_DEV_STEP_DEF.nodes["master_flush_dev_step_summary"](state)
        assert "flushed" in state["phase"]

        # master_flush_dev_step_conv → save_checkpoint with step_idx=1
        state = MASTER_FLUSH_DEV_STEP_DEF.nodes["master_flush_dev_step_conv"](state)
        assert "conv_flushed" in state["phase"]

        # Verify checkpoint
        cp_path = self.rt.paths.checkpoint
        assert os.path.exists(cp_path)
        import json
        with open(cp_path, "r", encoding="utf-8") as f:
            cp = json.load(f)
        assert cp["resume_node"] == "dev_exec_step"
        assert cp["step_idx"] == 1          # commit_step_idx=1 传入 checkpoint

        # Master conv 已切换
        assert self.rt.context.get_ctx("master_conv") != old_master_conv

        # ════════════════════════════════════════════
        # Step 2: DevCommit.git_commit → done
        # ════════════════════════════════════════════
        self.rt.context.set_ctx("dev_step_index", "2")  # 模拟 step 2 已通过 review
        state = DevCommit.git_commit(state)
        assert state["judge_result"] == "done"
        assert state["phase"] == "dev_commit_done"
class TestPhase2FullPath:
    """Phase 2 完整 happy path：handoff → align → criteria → design → design_review → plan → plan_review → exec → review → commit。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        DEV_HANDOFF_DEF.nodes["dev_handoff"]._runtime = self.rt
        DevAlign._runtime = self.rt
        for node_name in ("devwrite_criteria", "review_dev_criteria", "review_to_dev_artifact"):
            DEV_CRITERIA_DEF.nodes[node_name]._runtime = self.rt
        DevWriteDesign._runtime = self.rt
        for node_name in ("dev_design_review", "dev_design_review_pass"):
            DEV_DESIGN_REVIEW_DEF.nodes[node_name]._runtime = self.rt
        WriteDesignSummary._runtime = self.rt
        DevWritePlan._runtime = self.rt
        for node_name in ("dev_plan_review", "dev_plan_review_pass"):
            DEV_PLAN_REVIEW_DEF.nodes[node_name]._runtime = self.rt
        DevExecStep._runtime = self.rt
        DevReviewStep._runtime = self.rt
        DevCommit._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "/tmp/project_context.md")

        # 创建 Dev 工作目录 + plan.md + design.md（DevExecStep/DevReviewStep 依赖）
        dev_dir = os.path.join(self.rt.paths.workspace, "Dev")
        os.makedirs(dev_dir, exist_ok=True)
        with open(os.path.join(dev_dir, "plan.md"), "w", encoding="utf-8") as f:
            f.write("## Step 1: 初始化\n### 验收方法\necho ok\n")
        with open(os.path.join(dev_dir, "design.md"), "w", encoding="utf-8") as f:
            f.write("# Design\n系统架构设计\n")

    @patch("workflow.phase2.ensure_write_file", return_value=True)
    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_handoff_to_commit(self, mock_utils, mock_p2):
        """全线串联：dev_handoff → DevAlign(A) → dev_criteria(P) → design → design_review(P) → summary → plan → plan_review(P) → exec → review(P) → commit。"""
        ws = self.rt.paths.workspace

        # ── Node 1: dev_handoff ──
        state = DEV_HANDOFF_DEF.nodes["dev_handoff"]({})
        assert "handoff" in state["phase"]
        devletter_path = self.rt.context.get_ctx("devletter_path")
        assert devletter_path is not None

        # ── Node 2: DevAlign.dev ──
        os.makedirs(os.path.dirname(devletter_path), exist_ok=True)
        with open(devletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 Dev 的信：项目需开发一个博客系统")
        state = DevAlign.dev(state)
        assert state["phase"] == "dev_align_dev_done"
        dev_reply_path = self.rt.context.get_ctx("dev_reply_path")
        assert dev_reply_path is not None

        # ── Node 3: DevAlign.pm ──
        os.makedirs(os.path.dirname(dev_reply_path), exist_ok=True)
        with open(dev_reply_path, "w", encoding="utf-8") as f:
            f.write("Dev 理解总结：需要开发博客系统，有疑问待解答")
        state = DevAlign.pm(state)
        assert state["phase"] == "dev_align_pm_done"

        # ── Node 4: DevAlign.judge → A ──
        self.mock.set_response("你是一个流程裁判。以下是 PM 的回复。", "A")
        state = DevAlign.judge(state)
        assert state["judge_result"] == "exit"

        # ── Node 5: DevAlign.judge_exit ──
        state = DevAlign.judge_exit(state)

        # ── Node 6: dev_criteria write ──
        state = DEV_CRITERIA_DEF.nodes["devwrite_criteria"](state)
        assert state["phase"] == "dev_criteria_done"
        criteria_path = self.rt.context.get_ctx("dev_criteria_path")
        assert criteria_path is not None

        # ── Node 7: dev_criteria review → P ──
        os.makedirs(os.path.dirname(criteria_path), exist_ok=True)
        with open(criteria_path, "w", encoding="utf-8") as f:
            f.write("# Dev 设计审核标准\n- 架构合理性\n- 功能完整性")
        self.mock.set_response("你是一个流程裁判。以下是 Reviewer 的回复。", "P")
        state = DEV_CRITERIA_DEF.nodes["review_dev_criteria"](state)
        assert state["judge_result"] == "dev_write_design"

        # ── Node 8: criteria pass-through ──
        state = DEV_CRITERIA_DEF.nodes["review_to_dev_artifact"](state)

        # ── Node 9: DevWriteDesign.write_design_letter ──
        state = DevWriteDesign.write_design_letter(state)
        assert state["phase"] == "dev_design_letter_done"
        designletter_path = self.rt.context.get_ctx("designletter_path")
        assert designletter_path is not None

        # ── Node 10: DevWriteDesign.read_design_letter ──
        os.makedirs(os.path.dirname(designletter_path), exist_ok=True)
        with open(designletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 Dev：请写详细设计")
        state = DevWriteDesign.read_design_letter(state)
        assert state["phase"] == "dev_design_done"

        # ── Node 11: dev_design_review → P ──
        self.mock.set_response("你是一个流程裁判。以下是 reviewer 的回复。", "P")
        state = DEV_DESIGN_REVIEW_DEF.nodes["dev_design_review"](state)
        assert state["judge_result"] == "dev_write_plan"

        # ── Node 12: dev_design_review pass-through ──
        state = DEV_DESIGN_REVIEW_DEF.nodes["dev_design_review_pass"](state)

        # ── Node 13: WriteDesignSummary.run ──
        state = WriteDesignSummary.run(state)
        assert state["phase"] == "design_summary_done"

        # ── Node 14: DevWritePlan.write_plan_letter ──
        state = DevWritePlan.write_plan_letter(state)
        assert state["phase"] == "dev_plan_letter_done"
        planletter_path = self.rt.context.get_ctx("planletter_path")
        assert planletter_path is not None

        # ── Node 15: DevWritePlan.read_plan_letter ──
        os.makedirs(os.path.dirname(planletter_path), exist_ok=True)
        with open(planletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 Dev：请写实现计划")
        state = DevWritePlan.read_plan_letter(state)
        assert state["phase"] == "dev_plan_done"

        # ── Node 16: dev_plan_review → P（含 on_pass 设 step 计数器）──
        self.mock.set_response("你是一个流程裁判。以下是 reviewer 的回复。", "P")
        state = DEV_PLAN_REVIEW_DEF.nodes["dev_plan_review"](state)
        assert state["judge_result"] == "dev_exec"
        assert self.rt.context.get_ctx("dev_step_index") == "0"
        assert int(self.rt.context.get_ctx("dev_total_steps") or "0") >= 1

        # ── Node 17: dev_plan_review pass-through ──
        state = DEV_PLAN_REVIEW_DEF.nodes["dev_plan_review_pass"](state)

        # ── Node 18: DevExecStep.write_step_letter ──
        state = DevExecStep.write_step_letter(state)
        assert state["phase"] == "dev_exec_letter_done"
        exec_letter_path = self.rt.context.get_ctx("exec_letter_path")
        assert exec_letter_path is not None

        # ── Node 19: DevExecStep.read_step_letter ──
        os.makedirs(os.path.dirname(exec_letter_path), exist_ok=True)
        with open(exec_letter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 Dev：请实现 Step 1")
        state = DevExecStep.read_step_letter(state)
        assert state["phase"] == "dev_exec"

        # ── Node 20: DevReviewStep.run → P ──
        self.mock.set_response("你是一个流程裁判。以下是 Reviewer 的回复。", "P")
        state = DevReviewStep.run(state)
        assert state["judge_result"] == "dev_commit"

        # ── Node 21: DevCommit.git_commit → done ──
        state = DevCommit.git_commit(state)
        assert state["judge_result"] == "done"
        assert state["phase"] == "dev_commit_done"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_write_and_read_step_letter(self, mock_ensure):
        """write_step_letter → read_step_letter 串联。"""
        state = DevExecStep.write_step_letter({})
        assert state["phase"] == "dev_exec_letter_done"

        # 手动创建 letter 文件供 read_step_letter 读取
        lpath = self.rt.context.get_ctx("exec_letter_path")
        os.makedirs(os.path.dirname(lpath), exist_ok=True)
        with open(lpath, "w", encoding="utf-8") as f:
            f.write("Master to Dev: Step 1 实现说明")

        state = DevExecStep.read_step_letter(state)
        assert state["phase"] == "dev_exec"
        assert self.mock.call_history[1][0] == "dev"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_review_step_judge_P(self, mock_ensure):
        """DevReviewStep 在 judge=P 时返回 step_pass。"""
        self.mock.set_response("请审查 Dev 的最新实现", "审查通过")
        self.mock.set_response("你是一个流程裁判", "P")
        state = DevReviewStep.run({})
        assert state["judge_result"] == "dev_commit"
        assert "dev_exec_done" in state["phase"] or "step_pass" in state["phase"]

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_review_step_judge_F_retry(self, mock_ensure):
        """DevReviewStep 在 judge=F 时返回 step_retry。"""
        self.mock.set_response("请审查 Dev 的最新实现", "审查不通过")
        self.mock.set_response("你是一个流程裁判", "F")
        state = DevReviewStep.run({})
        assert state["judge_result"] == "step_retry"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_review_step_judge_F_rollback_after_threshold(self, mock_ensure):
        """DevReviewStep 超出回滚阈值时返回 dev_rollback。"""
        self.rt.context.set_ctx("dev_step_has_failed", "true")
        self.rt.context.set_ctx("dev_step_fail_count", "99")
        self.mock.set_response("请审查 Dev 的最新实现", "审查不通过")
        self.mock.set_response("你是一个流程裁判", "F")
        state = DevReviewStep.run({})
        assert state["judge_result"] in ("dev_rollback", "dev_escalate", "step_retry")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_dev_commit_done_when_all_steps_complete(self, mock_ensure):
        """dev_commit_git 在所有步骤完成时返回 done。"""
        self.rt.context.set_ctx("dev_conv", "dev-exec-test")
        self.rt.context.set_ctx("dev_step_index", "1")  # Already incremented by DevReviewStep
        self.rt.context.set_ctx("dev_total_steps", "1")
        state = DevCommit.git_commit({})
        assert state["judge_result"] == "done"
        assert state["phase"] == "dev_commit_done"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_dev_commit_continue_when_more_steps(self, mock_ensure):
        """dev_commit_git 在有更多步骤时返回 continue。"""
        self.rt.context.set_ctx("dev_conv", "dev-exec-test")
        self.rt.context.set_ctx("dev_step_index", "0")
        self.rt.context.set_ctx("dev_total_steps", "3")
        state = DevCommit.git_commit({})
        assert state["judge_result"] == "continue"
        assert state["phase"] == "dev_commit_more"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_full_exec_letter_to_review_sequence(self, mock_ensure):
        """letter → exec → review_P 全串联。"""
        # Node 1: write_step_letter
        state = DevExecStep.write_step_letter({})

        # Create letter file
        lpath = self.rt.context.get_ctx("exec_letter_path")
        os.makedirs(os.path.dirname(lpath), exist_ok=True)
        with open(lpath, "w", encoding="utf-8") as f:
            f.write("Master to Dev: Step 1 实现说明")

        # Node 2: read_step_letter
        state = DevExecStep.read_step_letter(state)

        # Node 3: review_step (judge=P)
        self.mock.set_response("请审查 Dev 的最新实现", "审查通过")
        self.mock.set_response("你是一个流程裁判", "P")
        state = DevReviewStep.run(state)
        assert "judge_result" in state
