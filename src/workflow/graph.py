"""Graph — LangGraph 图构建与入口。"""
import os, sys

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .utils import WorkflowState, setup_runtime, interruptible
from .phase0 import PreFlightClarify
from .phase1 import (PM_HANDOFF_DEF, PM_CRITERIA_DEF, PMAlign, MasterReplyPM, JudgeMasterReply, ClarifyInject,
                     PMWriteDoc, ReviewPMOutput, HumanReview)
from .phase2 import (DEV_HANDOFF_DEF, DEV_CRITERIA_DEF, DevAlign,
                     DevWriteDesign, DEV_DESIGN_REVIEW_DEF,
                     DevWritePlan, DEV_PLAN_REVIEW_DEF, DevGitInit, DevExecStep,
                     DevReviewStep, DevCommit, DevRollback, DevEscalate)
from .phase3 import (QA_HANDOFF_DEF, QA_CRITERIA_DEF, QAAlign,
                     QAWriteTestPlan, MASTER_PLAN_REVIEW_DEF, QAWriteTestCase,
                     REVIEWER_CODE_REVIEW_DEF, QARunTests, JudgeTestResult, DevFix)
from .flush import (MASTER_FLUSH_CLARIFY_DEF, MASTER_FLUSH_PM_DEF,
                     MASTER_FLUSH_DEV_DEF, MASTER_FLUSH_QA_DEF)
from .checkpoint import ResumeRouter
from .phase4 import ConsistencyAudit, WriteMaintenanceDocs, DeliverySummary

NODES = [
]


def build_graph(runtime) -> StateGraph:
    """构建 LangGraph StateGraph。"""
    for f in NODES:
        f._runtime = runtime
        # interruptible 包装的函数，_runtime 也要透传给原始函数
        if hasattr(f, '__wrapped__'):
            f.__wrapped__._runtime = runtime

    graph = StateGraph(WorkflowState)

    # ── 注册所有节点 ──
    for f in NODES:
        graph.add_node(f.__name__, f)
    ResumeRouter.register(graph, runtime)
    PreFlightClarify.register(graph, runtime)
    pm_handoff = PM_HANDOFF_DEF.register(graph, runtime)
    PMAlign.register(graph, runtime)
    MasterReplyPM.register(graph, runtime)
    JudgeMasterReply.register(graph, runtime)
    ClarifyInject.register(graph, runtime)
    pm_criteria = PM_CRITERIA_DEF.register(graph, runtime)
    PMWriteDoc.register(graph, runtime)
    ReviewPMOutput.register(graph, runtime)
    HumanReview.register(graph, runtime)
    dev_handoff = DEV_HANDOFF_DEF.register(graph, runtime)
    DevAlign.register(graph, runtime)
    dev_criteria = DEV_CRITERIA_DEF.register(graph, runtime)
    DevWriteDesign.register(graph, runtime)
    dev_design_review = DEV_DESIGN_REVIEW_DEF.register(graph, runtime)
    DevWritePlan.register(graph, runtime)
    dev_plan_review = DEV_PLAN_REVIEW_DEF.register(graph, runtime)
    DevGitInit.register(graph, runtime)
    DevExecStep.register(graph, runtime)
    DevReviewStep.register(graph, runtime)
    DevCommit.register(graph, runtime)
    DevRollback.register(graph, runtime)
    DevEscalate.register(graph, runtime)
    master_flush_clarify = MASTER_FLUSH_CLARIFY_DEF.register(graph, runtime)
    master_flush_pm = MASTER_FLUSH_PM_DEF.register(graph, runtime)
    master_flush_dev = MASTER_FLUSH_DEV_DEF.register(graph, runtime)
    qa_handoff = QA_HANDOFF_DEF.register(graph, runtime)
    QAAlign.register(graph, runtime)
    qa_criteria = QA_CRITERIA_DEF.register(graph, runtime)
    QAWriteTestPlan.register(graph, runtime)
    master_plan_review = MASTER_PLAN_REVIEW_DEF.register(graph, runtime)
    QAWriteTestCase.register(graph, runtime)
    reviewer_code_review = REVIEWER_CODE_REVIEW_DEF.register(graph, runtime)
    QARunTests.register(graph, runtime)
    JudgeTestResult.register(graph, runtime)
    DevFix.register(graph, runtime)
    master_flush_qa = MASTER_FLUSH_QA_DEF.register(graph, runtime)
    ConsistencyAudit.register(graph, runtime)
    WriteMaintenanceDocs.register(graph, runtime)
    DeliverySummary.register(graph, runtime)

    graph.set_entry_point(ResumeRouter.entries["router"])

    # ── resume 节点 → 实际工作节点（ResumeRouter 内部已处理条件路由）──
    graph.add_edge(ResumeRouter.exits["to_pre_flight"], PreFlightClarify.entries["init"])
    graph.add_edge(ResumeRouter.exits["resume_pm"], pm_handoff.entries["run"])
    graph.add_edge(ResumeRouter.exits["resume_dev"], dev_handoff.entries["run"])
    graph.add_edge(ResumeRouter.exits["resume_qa"], qa_handoff.entries["run"])
    graph.add_edge(ResumeRouter.exits["resume_dev_exec"], DevExecStep.entries["run"])

    # ── Phase 0 跨阶段边 ──
    graph.add_edge(PreFlightClarify.exits["close"], master_flush_clarify.entries["write_summary"])
    graph.add_edge(master_flush_clarify.exits["flush_conv"], pm_handoff.entries["run"])

    # ── Phase 1: PM 出方案 ──
    graph.add_edge(pm_handoff.exits["run"], PMAlign.entries["read"])
    graph.add_edge(PMAlign.exits["read"], MasterReplyPM.entries["run"])
    graph.add_edge(MasterReplyPM.exits["run"], JudgeMasterReply.entries["run"])
    graph.add_conditional_edges(JudgeMasterReply.exits["run"], lambda s: s.get("judge_result", ""), {
        "A": pm_criteria.entries["run"],
        "B": PMAlign.entries["master_reply"],
        "C": ClarifyInject.entries["interact"],
    })
    graph.add_edge(ClarifyInject.exits["record"], MasterReplyPM.entries["run"])
    graph.add_edge(pm_criteria.exits["pass"], PMWriteDoc.entries["write_prd_letter"])
    graph.add_edge(PMWriteDoc.exits["read_proto_letter"], ReviewPMOutput.entries["run"])
    graph.add_conditional_edges(ReviewPMOutput.exits["run"], lambda s: s.get("judge_result", ""), {
        "human_review": HumanReview.entries["run"],
        "pm_write_doc": PMWriteDoc.entries["write_prd_letter"],
    })
    graph.add_conditional_edges(HumanReview.exits["run"], lambda s: s.get("judge_result", ""), {
        END: master_flush_pm.entries["write_summary"],
        "review_pm_output": ReviewPMOutput.entries["run"],
    })

    # ── Phase 2: Dev 出设计 + 编码执行 ──
    graph.add_edge(master_flush_pm.exits["flush_conv"], dev_handoff.entries["run"])
    graph.add_edge(dev_handoff.exits["run"], DevAlign.entries["dev"])
    graph.add_edge(DevAlign.exits["judge_exit"], dev_criteria.entries["run"])
    graph.add_edge(dev_criteria.exits["pass"], DevWriteDesign.entries["run"])
    graph.add_edge(DevWriteDesign.exits["run"], dev_design_review.entries["run"])
    graph.add_edge(dev_design_review.exits["pass"], DevWritePlan.entries["run"])
    graph.add_edge(dev_design_review.exits["fail"], DevWriteDesign.entries["run"])
    graph.add_edge(DevWritePlan.exits["run"], dev_plan_review.entries["run"])
    graph.add_edge(dev_plan_review.exits["pass"], DevGitInit.entries["run"])
    graph.add_edge(dev_plan_review.exits["fail"], DevWritePlan.entries["run"])
    graph.add_edge(DevGitInit.exits["run"], DevExecStep.entries["run"])
    graph.add_edge(DevExecStep.exits["run"], DevReviewStep.entries["run"])
    graph.add_conditional_edges(DevReviewStep.exits["run"], lambda s: s.get("judge_result", ""), {
        "dev_commit": DevCommit.entries["run"],
        "step_retry": DevExecStep.entries["run"],
        "dev_rollback": DevRollback.entries["run"],
        "dev_escalate": DevEscalate.entries["run"],
    })
    graph.add_conditional_edges(DevCommit.exits["run"], lambda s: s.get("judge_result", ""), {
        "dev_exec_step": DevExecStep.entries["run"],
        "done": master_flush_dev.entries["write_summary"],
    })
    graph.add_edge(DevRollback.exits["run"], DevExecStep.entries["run"])
    graph.add_edge(DevEscalate.exits["run"], DevExecStep.entries["run"])

    # ── Phase 3: QA ──
    graph.add_edge(master_flush_dev.exits["flush_conv"], qa_handoff.entries["run"])
    graph.add_edge(qa_handoff.exits["run"], QAAlign.entries["qa_read"])
    graph.add_edge(QAAlign.exits["judge_exit"], qa_criteria.entries["run"])
    graph.add_edge(qa_criteria.exits["pass"], QAWriteTestPlan.entries["run"])
    graph.add_edge(QAWriteTestPlan.exits["run"], master_plan_review.entries["run"])
    graph.add_edge(master_plan_review.exits["pass"], QAWriteTestCase.entries["run"])
    graph.add_edge(master_plan_review.exits["fail"], QAWriteTestPlan.entries["run"])
    graph.add_edge(QAWriteTestCase.exits["run"], reviewer_code_review.entries["run"])
    graph.add_edge(reviewer_code_review.exits["pass"], QARunTests.entries["run"])
    graph.add_edge(reviewer_code_review.exits["fail"], QAWriteTestCase.entries["run"])
    graph.add_edge(QARunTests.exits["run"], JudgeTestResult.entries["judge"])
    graph.add_edge(JudgeTestResult.exits["to_flush"], master_flush_qa.entries["write_summary"])
    graph.add_edge(JudgeTestResult.exits["to_dev_fix"], DevFix.entries["run"])
    graph.add_edge(DevFix.exits["run"], QARunTests.entries["run"])
    graph.add_edge(master_flush_qa.exits["flush_conv"], ConsistencyAudit.entries["run"])

    # ── Phase 4: 交付 ──
    graph.add_edge(ConsistencyAudit.exits["run"], WriteMaintenanceDocs.entries["run"])
    graph.add_edge(WriteMaintenanceDocs.exits["run"], DeliverySummary.entries["run"])
    graph.add_edge(DeliverySummary.exits["run"], END)

    # ── Resume → Phase 4 ──
    graph.add_edge(ResumeRouter.exits["resume_phase4"], ConsistencyAudit.entries["run"])

    return graph.compile(checkpointer=MemorySaver())


def draw_graph(app):
    """生成工作流图并保存为 PNG。"""
    output = os.path.join(os.getcwd(), "workflow_diagram.png")
    try:
        png = app.get_graph().draw_mermaid_png()
        with open(output, "wb") as f:
            f.write(png)
        print(f"  → 流程图已保存: {output}")
    except Exception as e:
        print(f"  → 生成 PNG 失败（{e}），尝试 Mermaid 文本...")
        try:
            mermaid = app.get_graph().draw_mermaid()
            md_path = output.replace(".png", ".md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("```mermaid\n" + mermaid + "\n```")
            print(f"  → Mermaid 图已保存: {md_path}")
        except Exception:
            print("  → 无法生成流程图（需安装 pyppeteer 或 playwright）")


def _init_state() -> WorkflowState:
    return {"phase": "pre_flight", "judge_result": ""}


def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="AI Coding 工作流框架")
    p.add_argument("--config", default=None,
                   help="配置文件路径（默认: 项目根目录/runtime_config.json）")
    return p.parse_args()


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312", "gb18030"):
        sys.stdout.reconfigure(errors="replace")
    print("=" * 60)
    print("  AI Coding 工作流框架 — 骨架")
    print("=" * 60)

    args = parse_args()
    config_path = args.config or os.path.join(os.getcwd(), "runtime_config.json")

    print("\n[1/2] 初始化 AgentRuntime...")
    runtime = setup_runtime(config_path)

    print("\n[2/2] 构建并运行 LangGraph...")
    app = build_graph(runtime)
    state = _init_state()
    stream_config = {"configurable": {"thread_id": "workflow-1"}}

    hotkey = runtime.interaction.interrupt_hotkey or ""
    if hotkey:
        from .utils import start_interrupt_listener, stop_interrupt_listener
        start_interrupt_listener(hotkey)

    try:
        for event in app.stream(state, stream_config):
            for node_name, node_state in event.items():
                if node_state is None:
                    print(f"  [{node_name}] 完成")
                    continue
                print(f"  [{node_name}] phase={node_state.get('phase', '?')}, "
                      f"judge={node_state.get('judge_result', '')[:20]}")
    except KeyboardInterrupt:
        print("\n  [中断] 用户按 Ctrl+C 终止工作流")
        sys.exit(1)
    finally:
        if hotkey:
            stop_interrupt_listener()

    print("\n✅ 框架就绪")
