"""跨 Phase flush 边界测试。"""
from __future__ import annotations
from unittest.mock import patch
import json
import os
import pytest
from agent_runtime import AgentRuntime
from workflow.phase0 import PreFlightClarify
from workflow.phase1 import PM_HANDOFF_DEF
from workflow.phase2 import DEV_HANDOFF_DEF
from workflow.phase3 import QA_HANDOFF_DEF
from workflow.phase4 import ConsistencyAudit
from workflow.flush import (MASTER_FLUSH_CLARIFY_DEF, MASTER_FLUSH_PM_DEF,
                             MASTER_FLUSH_DEV_DEF, MASTER_FLUSH_QA_DEF)


class TestFlushClarifyToPM:
    """Phase 0 → Phase 1 flush 边界：close → flush → pm_handoff。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PreFlightClarify._runtime = self.rt
        for n in ("master_flush_clarify_summary", "master_flush_clarify_conv"):
            MASTER_FLUSH_CLARIFY_DEF.nodes[n]._runtime = self.rt
        PM_HANDOFF_DEF.nodes["pm_handoff"]._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "/tmp/project_context.md")
        self.rt.context.set_ctx("clarify_reason", "用户确认完成")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_flush_clarify_to_pm(self, mock_flush, mock_utils):
        """close → flush → pm_handoff 串联，验证 checkpoint。"""
        ws = self.rt.paths.workspace

        # ── PreFlightClarify.close ──
        state = PreFlightClarify.close({})
        assert state["phase"] == "done"

        # ── Flush write_summary ──
        state = MASTER_FLUSH_CLARIFY_DEF.nodes["master_flush_clarify_summary"](state)
        assert "flushed" in state["phase"]
        summary_path = self.rt.context.get_ctx("phase_summary_path")
        assert summary_path is not None

        # ── Flush flush_conv ──
        state = MASTER_FLUSH_CLARIFY_DEF.nodes["master_flush_clarify_conv"](state)
        assert "conv_flushed" in state["phase"]

        # ── Checkpoint assertion ──
        cp_path = self.rt.paths.checkpoint
        assert os.path.exists(cp_path)
        with open(cp_path, "r", encoding="utf-8") as f:
            cp = json.load(f)
        assert cp["resume_node"] == "pm_handoff"

        # ── Master conv switched ──
        new_conv = self.rt.context.get_ctx("master_conv")
        assert new_conv != "master-test"

        # ── pm_handoff 可执行 ──
        state = PM_HANDOFF_DEF.nodes["pm_handoff"](state)
        assert "handoff" in state["phase"]


class TestFlushPMToDev:
    """Phase 1 → Phase 2 flush 边界：flush → dev_handoff。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        for n in ("master_flush_pm_summary", "master_flush_pm_conv"):
            MASTER_FLUSH_PM_DEF.nodes[n]._runtime = self.rt
        DEV_HANDOFF_DEF.nodes["dev_handoff"]._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "/tmp/project_context.md")

        # 创建 PM 产物（ReviewPMOutput 后应有）
        pm_dir = os.path.join(self.rt.paths.workspace, "PM")
        os.makedirs(pm_dir, exist_ok=True)
        with open(os.path.join(pm_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write("# PRD\n博客系统需求")
        with open(os.path.join(pm_dir, "prototype.html"), "w", encoding="utf-8") as f:
            f.write("<html>prototype</html>")
        with open(os.path.join(self.rt.paths.workspace, "criteria-pm.md"),
                  "w", encoding="utf-8") as f:
            f.write("# PM 审核标准")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_flush_pm_to_dev(self, mock_flush, mock_utils):
        """flush_pm → dev_handoff。"""
        # ── Flush write_summary ──
        state = MASTER_FLUSH_PM_DEF.nodes["master_flush_pm_summary"]({})
        assert "flushed" in state["phase"]
        summary_path = self.rt.context.get_ctx("phase_summary_path")
        assert summary_path is not None

        # ── Flush flush_conv ──
        state = MASTER_FLUSH_PM_DEF.nodes["master_flush_pm_conv"](state)
        assert "conv_flushed" in state["phase"]

        # ── Checkpoint verification ──
        cp_path = self.rt.paths.checkpoint
        assert os.path.exists(cp_path)
        with open(cp_path, "r", encoding="utf-8") as f:
            cp = json.load(f)
        assert cp["resume_node"] == "dev_handoff"

        # ── dev_handoff 可执行 ──
        state = DEV_HANDOFF_DEF.nodes["dev_handoff"](state)
        assert "handoff" in state["phase"]


class TestFlushDevToQA:
    """Phase 2 → Phase 3 flush 边界：flush → qa_handoff。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        for n in ("master_flush_dev_summary", "master_flush_dev_conv"):
            MASTER_FLUSH_DEV_DEF.nodes[n]._runtime = self.rt
        QA_HANDOFF_DEF.nodes["qa_handoff"]._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "/tmp/project_context.md")

        # 创建 Dev 产物
        dev_dir = os.path.join(self.rt.paths.workspace, "Dev")
        os.makedirs(dev_dir, exist_ok=True)
        with open(os.path.join(dev_dir, "design.md"), "w", encoding="utf-8") as f:
            f.write("# Design\n系统架构")
        with open(os.path.join(dev_dir, "plan.md"), "w", encoding="utf-8") as f:
            f.write("## Step 1\n验收方法: echo ok")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    def test_flush_dev_to_qa(self, mock_flush, mock_utils):
        """flush_dev → qa_handoff。"""
        # ── Flush write_summary ──
        state = MASTER_FLUSH_DEV_DEF.nodes["master_flush_dev_summary"]({})
        assert "flushed" in state["phase"]
        summary_path = self.rt.context.get_ctx("phase_summary_path")
        assert summary_path is not None

        # ── Flush flush_conv ──
        state = MASTER_FLUSH_DEV_DEF.nodes["master_flush_dev_conv"](state)
        assert "conv_flushed" in state["phase"]

        # ── Checkpoint verification ──
        cp_path = self.rt.paths.checkpoint
        assert os.path.exists(cp_path)
        with open(cp_path, "r", encoding="utf-8") as f:
            cp = json.load(f)
        assert cp["resume_node"] == "qa_handoff"

        # ── qa_handoff 可执行 ──
        state = QA_HANDOFF_DEF.nodes["qa_handoff"](state)
        assert "handoff" in state["phase"]


class TestFlushQAToPhase4:
    """Phase 3 → Phase 4 flush 边界：flush → ConsistencyAudit。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        for n in ("master_flush_qa_summary", "master_flush_qa_conv"):
            MASTER_FLUSH_QA_DEF.nodes[n]._runtime = self.rt
        ConsistencyAudit._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "/tmp/project_context.md")

        # 创建 QA 产物
        qa_dir = os.path.join(self.rt.paths.workspace, "QA")
        os.makedirs(qa_dir, exist_ok=True)
        os.makedirs(os.path.join(qa_dir, "tests"), exist_ok=True)
        with open(os.path.join(qa_dir, "test-plan.md"), "w", encoding="utf-8") as f:
            f.write("# 测试计划\nE2E 测试")
        with open(os.path.join(qa_dir, "test-report.md"), "w", encoding="utf-8") as f:
            f.write("# 测试报告\n全部通过")

    @patch("workflow.subgraphs.master_flush.ensure_write_file", return_value=True)
    @patch("workflow.phase4.ensure_write_file", return_value=True)
    def test_flush_qa_to_phase4(self, mock_p4, mock_flush):
        """flush_qa → ConsistencyAudit.run。"""
        # ── Flush write_summary ──
        state = MASTER_FLUSH_QA_DEF.nodes["master_flush_qa_summary"]({})
        assert "flushed" in state["phase"]
        summary_path = self.rt.context.get_ctx("phase_summary_path")
        assert summary_path is not None

        # ── Flush flush_conv ──
        state = MASTER_FLUSH_QA_DEF.nodes["master_flush_qa_conv"](state)
        assert "conv_flushed" in state["phase"]

        # ── Checkpoint verification ──
        cp_path = self.rt.paths.checkpoint
        assert os.path.exists(cp_path)
        with open(cp_path, "r", encoding="utf-8") as f:
            cp = json.load(f)
        assert cp["resume_node"] == "consistency_audit"

        # ── ConsistencyAudit 可执行 ──
        state = ConsistencyAudit.run(state)
        assert state["phase"] == "audit_done"
        assert self.rt.context.get_ctx("audit_path") is not None
