"""Phase 1 集成测试：PM 方案阶段线性段。"""
from __future__ import annotations
from unittest.mock import patch
import os
import pytest
from agent_runtime import AgentRuntime
from langgraph.graph import END
from workflow.phase1 import (PMAlign, MasterReplyPM, JudgeMasterReply,
                             PM_HANDOFF_DEF, PM_CRITERIA_DEF,
                             PMWriteDoc, ReviewPMOutput, HumanReview)


class TestPhase1HandoffAlignSegment:
    """PM handoff → align_read → master_reply → judge 线性段。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        PM_HANDOFF_DEF.nodes["pm_handoff"]._runtime = self.rt
        PMAlign._runtime = self.rt
        MasterReplyPM._runtime = self.rt
        JudgeMasterReply._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "/tmp/project_context.md")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_pm_handoff_writes_letter_path(self, mock_ensure):
        """pm_handoff 写信并将路径存入 context。"""
        state = PM_HANDOFF_DEF.nodes["pm_handoff"]({})
        assert "pm_handoff" in state["phase"]
        assert self.rt.context.get_ctx("pmletter_path") is not None
        assert self.mock.call_history[0][0] == "master"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_pm_align_read_uses_handoff_letter(self, mock_ensure):
        """pm_align_read 读取 handoff 信件并调 PM agent。"""
        lpath = os.path.join(self.rt.paths.handoffs, "master-to-pm-letter.md")
        os.makedirs(os.path.dirname(lpath), exist_ok=True)
        with open(lpath, "w", encoding="utf-8") as f:
            f.write("PM 测试信件内容")
        self.rt.context.set_ctx("pmletter_path", lpath)

        state = PMAlign.read({})
        assert state["phase"] == "pm_align_done"
        assert self.rt.context.get_ctx("pm_reply_path") is not None

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_master_reply_pm_via_context_fallback(self, mock_ensure):
        """master_reply_pm 在无回信文件时从 context 读取回复。"""
        self.rt.context.set_ctx("pm_reply_text", "PM 的理解内容：项目需要做...")
        state = MasterReplyPM.run({})
        assert state["phase"] == "master_reply_done"
        assert self.rt.context.get_ctx("master_reply") is not None
        assert self.mock.call_history[0][0] == "master"

    def test_judge_master_reply_A(self):
        """judge_master_reply 返回 A。"""
        self.mock.set_response("你是一个流程裁判", "A")
        self.rt.context.set_ctx("master_reply", "PM 理解完全正确")
        state = JudgeMasterReply.run({})
        assert state["judge_result"] == "A"

    def test_judge_master_reply_B(self):
        """judge_master_reply 返回 B。"""
        self.mock.set_response("你是一个流程裁判", "B")
        self.rt.context.set_ctx("master_reply", "PM 需要修正理解")
        state = JudgeMasterReply.run({})
        assert state["judge_result"] == "B"

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_handoff_to_judge_full_sequence(self, mock_ensure):
        """handoff → align_read → master_reply → judge 全串联。"""
        lpath = os.path.join(self.rt.paths.handoffs, "master-to-pm-letter.md")
        os.makedirs(os.path.dirname(lpath), exist_ok=True)
        with open(lpath, "w", encoding="utf-8") as f:
            f.write("PM 测试信件")
        self.rt.context.set_ctx("pmletter_path", lpath)

        # PM pre-read: pm_align_read
        state = PMAlign.read({})

        # 手动写入回信文件供 master_reply_pm 读取
        reply_path = self.rt.context.get_ctx("pm_reply_path")
        if reply_path:
            os.makedirs(os.path.dirname(reply_path), exist_ok=True)
            with open(reply_path, "w", encoding="utf-8") as f:
                f.write("PM 回信：理解了项目需求...")

        # master_reply_pm
        state = MasterReplyPM.run(state)

        # judge
        self.mock.set_response("你是一个流程裁判", "A")
        state = JudgeMasterReply.run(state)
        assert state["judge_result"] == "A"


class TestPhase1FullHappyPath:
    """Phase 1 完整 happy path：handoff → align → judge(A) → criteria(P) → PRD+proto → review(P) → human_review(EOF)。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
        for node_name in ("pm_handoff",):
            PM_HANDOFF_DEF.nodes[node_name]._runtime = self.rt
        PMAlign._runtime = self.rt
        MasterReplyPM._runtime = self.rt
        JudgeMasterReply._runtime = self.rt
        for node_name in ("pmwrite_criteria", "review_pm_criteria", "review_to_pm_artifact"):
            PM_CRITERIA_DEF.nodes[node_name]._runtime = self.rt
        PMWriteDoc._runtime = self.rt
        ReviewPMOutput._runtime = self.rt
        HumanReview._runtime = self.rt

        self.rt.context.set_ctx("master_conv", "master-test")
        self.rt.context.set_bg("project_context_path", "/tmp/project_context.md")
        self.rt.context.set_ctx("pm_align_round", "0")

    @patch("workflow.utils.ensure_write_file", return_value=True)
    def test_handoff_to_human_review(self, mock_ensure):
        """全线串联：pm_handoff → PMAlign.read → MasterReplyPM → Judge(A) → write_criteria → review_criteria(P) → write_prd → read_prd → write_proto → read_proto → review_output(P) → human_review(EOF)。"""
        from unittest.mock import MagicMock

        ws = self.rt.paths.workspace

        # ── Node 1: pm_handoff ──
        state = PM_HANDOFF_DEF.nodes["pm_handoff"]({})
        assert "handoff" in state["phase"]
        pmletter_path = self.rt.context.get_ctx("pmletter_path")
        assert pmletter_path is not None

        # ── Node 2: PMAlign.read ──
        os.makedirs(os.path.dirname(pmletter_path), exist_ok=True)
        with open(pmletter_path, "w", encoding="utf-8") as f:
            f.write("Master 给 PM 的信：项目需开发一个博客系统")
        state = PMAlign.read(state)
        assert state["phase"] == "pm_align_done"
        pm_reply_path = self.rt.context.get_ctx("pm_reply_path")
        assert pm_reply_path is not None

        # ── Node 3: MasterReplyPM.run ──
        os.makedirs(os.path.dirname(pm_reply_path), exist_ok=True)
        with open(pm_reply_path, "w", encoding="utf-8") as f:
            f.write("PM 回信：理解了项目需求，需要开发登录注册功能")
        state = MasterReplyPM.run(state)
        assert state["phase"] == "master_reply_done"
        assert self.rt.context.get_ctx("master_reply") is not None

        # ── Node 4: JudgeMasterReply.run → A ──
        self.mock.set_response("你是一个流程裁判。以下是 Master 的回复。", "A")
        state = JudgeMasterReply.run(state)
        assert state["judge_result"] == "A"

        # ── Node 5: PM criteria write ──
        state = PM_CRITERIA_DEF.nodes["pmwrite_criteria"](state)
        assert state["phase"] == "pm_criteria_done"
        criteria_path = self.rt.context.get_ctx("pm_criteria_path")
        assert criteria_path is not None

        # ── Node 6: PM criteria review → P ──
        os.makedirs(os.path.dirname(criteria_path), exist_ok=True)
        with open(criteria_path, "w", encoding="utf-8") as f:
            f.write("# PM 审核标准\n- 需求完整性\n- MVP 边界")
        self.mock.set_response("你是一个流程裁判。以下是 Reviewer 的回复。", "P")
        state = PM_CRITERIA_DEF.nodes["review_pm_criteria"](state)
        assert state["judge_result"] == "pm_write_doc"

        # ── Node 7: criteria pass-through ──
        state = PM_CRITERIA_DEF.nodes["review_to_pm_artifact"](state)

        # ── Node 8: PMWriteDoc.write_prd_letter ──
        state = PMWriteDoc.write_prd_letter(state)
        assert state["phase"] == "pm_read_prd"
        prdletter_path = self.rt.context.get_ctx("prdletter_path")
        assert prdletter_path is not None

        # ── Node 9: PMWriteDoc.read_prd_letter ──
        os.makedirs(os.path.dirname(prdletter_path), exist_ok=True)
        with open(prdletter_path, "w", encoding="utf-8") as f:
            f.write("Master 要求 PM 输出 PRD.md")
        state = PMWriteDoc.read_prd_letter(state)
        assert state["phase"] == "pm_write_proto_letter"

        # ── Node 10: PMWriteDoc.write_proto_letter ──
        state = PMWriteDoc.write_proto_letter(state)
        assert state["phase"] == "pm_read_proto"
        protoletter_path = self.rt.context.get_ctx("protoletter_path")
        assert protoletter_path is not None

        # ── Node 11: PMWriteDoc.read_proto_letter ──
        os.makedirs(os.path.dirname(protoletter_path), exist_ok=True)
        with open(protoletter_path, "w", encoding="utf-8") as f:
            f.write("Master 要求 PM 编写 prototype.html")
        state = PMWriteDoc.read_proto_letter(state)
        assert state["phase"] == "done"

        # ── Node 12: ReviewPMOutput.run → P ──
        pm_dir = os.path.join(ws, "PM")
        os.makedirs(pm_dir, exist_ok=True)
        with open(os.path.join(pm_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write("# PRD\n博客系统需求文档")
        with open(os.path.join(pm_dir, "prototype.html"), "w", encoding="utf-8") as f:
            f.write("<html><body>原型</body></html>")
        os.makedirs(os.path.join(ws, "reviewer", "pm"), exist_ok=True)

        self.mock.set_response("你是一个流程裁判。以下是 Reviewer 的回复。", "P")
        state = ReviewPMOutput.run(state)
        assert state["judge_result"] == "human_review"
        assert state["phase"] == "review_done"

        # ── Node 13: HumanReview.run → EOF (pass) ──
        self.rt.checkpoint.wait = MagicMock(return_value=MagicMock(message=""))
        state = HumanReview.run(state)
        assert state["judge_result"] == END
        assert state["phase"] == "done"
