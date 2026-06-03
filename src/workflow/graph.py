"""Graph — LangGraph 图构建与入口。"""
import os, sys

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .utils import WorkflowState, setup_runtime, interruptible
from .phase0 import PreFlightClarify
from .phase1 import (PMHandoff, PMAlign, MasterReplyPM, JudgeMasterReply, ClarifyInject,
                     PMWriteCriteria, ReviewPMCriteria, PMWriteDoc, ReviewPMOutput, HumanReview)
from .phase2 import (DevHandoff, DevAlign, DevWriteCriteria,
                     ReviewDevCriteria, DevWriteDesign, DevReviewDesign,
                     DevWritePlan, DevReviewPlan, DevGitInit, DevExecStep,
                     DevReviewStep, DevCommit, DevRollback, DevEscalate)
from .phase3 import qa_handoff, qa_align
from .flush import (MasterFlushClarify, MasterFlushPM, MasterFlushDev)
from .checkpoint import ResumeRouter

NODES = [
    interruptible(qa_handoff), interruptible(qa_align),
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
    PMHandoff.register(graph, runtime)
    PMAlign.register(graph, runtime)
    MasterReplyPM.register(graph, runtime)
    JudgeMasterReply.register(graph, runtime)
    ClarifyInject.register(graph, runtime)
    PMWriteCriteria.register(graph, runtime)
    ReviewPMCriteria.register(graph, runtime)
    PMWriteDoc.register(graph, runtime)
    ReviewPMOutput.register(graph, runtime)
    HumanReview.register(graph, runtime)
    DevHandoff.register(graph, runtime)
    DevAlign.register(graph, runtime)
    DevWriteCriteria.register(graph, runtime)
    ReviewDevCriteria.register(graph, runtime)
    DevWriteDesign.register(graph, runtime)
    DevReviewDesign.register(graph, runtime)
    DevWritePlan.register(graph, runtime)
    DevReviewPlan.register(graph, runtime)
    DevGitInit.register(graph, runtime)
    DevExecStep.register(graph, runtime)
    DevReviewStep.register(graph, runtime)
    DevCommit.register(graph, runtime)
    DevRollback.register(graph, runtime)
    DevEscalate.register(graph, runtime)
    MasterFlushClarify.register(graph, runtime)
    MasterFlushPM.register(graph, runtime)
    MasterFlushDev.register(graph, runtime)

    graph.set_entry_point(ResumeRouter.entries["router"])

    # ── resume 节点 → 实际工作节点（ResumeRouter 内部已处理条件路由）──
    graph.add_edge(ResumeRouter.exits["to_pre_flight"], PreFlightClarify.entries["init"])
    graph.add_edge(ResumeRouter.exits["resume_pm"], PMHandoff.entries["run"])
    graph.add_edge(ResumeRouter.exits["resume_dev"], "dev_handoff")
    graph.add_edge(ResumeRouter.exits["resume_qa"], "qa_handoff")
    graph.add_edge(ResumeRouter.exits["resume_dev_exec"], DevExecStep.entries["run"])

    # ── Phase 0 跨阶段边 ──
    graph.add_edge(PreFlightClarify.exits["close"], MasterFlushClarify.entries["write_summary"])
    graph.add_edge(MasterFlushClarify.exits["flush_conv"], PMHandoff.entries["run"])

    # ── Phase 1: PM 出方案 ──
    graph.add_edge(PMHandoff.exits["run"], PMAlign.entries["read"])
    graph.add_edge(PMAlign.exits["read"], MasterReplyPM.entries["run"])
    graph.add_edge(MasterReplyPM.exits["run"], JudgeMasterReply.entries["run"])
    graph.add_conditional_edges(JudgeMasterReply.exits["run"], lambda s: s.get("judge_result", ""), {
        "A": PMWriteCriteria.entries["run"],
        "B": PMAlign.entries["master_reply"],
        "C": ClarifyInject.entries["interact"],
    })
    graph.add_edge(ClarifyInject.exits["record"], MasterReplyPM.entries["run"])
    graph.add_conditional_edges(PMWriteCriteria.exits["run"], lambda s: s.get("judge_result", ""), {
        "review_pm_criteria": ReviewPMCriteria.entries["review"],
        "pmwrite_criteria": PMWriteCriteria.entries["run"],
    })
    graph.add_edge(ReviewPMCriteria.exits["to_pm_doc"], PMWriteDoc.entries["write_prd_letter"])
    graph.add_edge(ReviewPMCriteria.exits["write_feedback"], PMWriteCriteria.entries["run"])
    graph.add_edge(PMWriteDoc.exits["read_proto_letter"], ReviewPMOutput.entries["run"])
    graph.add_conditional_edges(ReviewPMOutput.exits["run"], lambda s: s.get("judge_result", ""), {
        "human_review": HumanReview.entries["run"],
        "pm_write_doc": PMWriteDoc.entries["write_prd_letter"],
    })
    graph.add_conditional_edges(HumanReview.exits["run"], lambda s: s.get("judge_result", ""), {
        END: MasterFlushPM.entries["write_summary"],
        "review_pm_output": ReviewPMOutput.entries["run"],
    })

    # ── Phase 2: Dev 出设计 + 编码执行 ──
    graph.add_edge(MasterFlushPM.exits["flush_conv"], DevHandoff.entries["run"])
    graph.add_edge(DevHandoff.exits["run"], DevAlign.entries["dev"])
    graph.add_edge(DevAlign.exits["judge_exit"], DevWriteCriteria.entries["run"])
    graph.add_conditional_edges(DevWriteCriteria.exits["run"], lambda s: s.get("judge_result", ""), {
        "review_dev_criteria": "review_dev_criteria",
        "devwrite_criteria": DevWriteCriteria.entries["run"],
    })
    graph.add_edge(ReviewDevCriteria.exits["to_dev_design"], DevWriteDesign.entries["run"])
    graph.add_edge(ReviewDevCriteria.exits["write_feedback"], DevWriteCriteria.entries["run"])
    graph.add_edge(DevWriteDesign.exits["run"], DevReviewDesign.entries["run"])
    graph.add_edge(DevReviewDesign.exits["run"], DevWritePlan.entries["run"])
    graph.add_edge(DevReviewDesign.exits["write_feedback"], DevWriteDesign.entries["run"])
    graph.add_edge(DevWritePlan.exits["run"], DevReviewPlan.entries["run"])
    graph.add_edge(DevReviewPlan.exits["run"], DevGitInit.entries["run"])
    graph.add_edge(DevReviewPlan.exits["write_feedback"], DevWritePlan.entries["run"])
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
        "done": MasterFlushDev.entries["write_summary"],
    })
    graph.add_edge(DevRollback.exits["run"], DevExecStep.entries["run"])
    graph.add_edge(DevEscalate.exits["run"], DevExecStep.entries["run"])

    # ── Phase 3: QA ──
    graph.add_edge(MasterFlushDev.exits["flush_conv"], "qa_handoff")
    graph.add_edge("qa_handoff", "qa_align")
    graph.add_edge("qa_align", END)

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
