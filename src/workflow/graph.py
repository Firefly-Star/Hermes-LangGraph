"""Graph — LangGraph 图构建与入口。"""
import os, sys

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .utils import WorkflowState, setup_runtime, interruptible
from .phase0 import PreFlightClarify
from .phase1 import (PMHandoff, PMAlign, MasterReplyPM, JudgeMasterReply, ClarifyInject,
                     PMWriteCriteria, ReviewPMCriteria, PMWriteDoc, ReviewPMOutput, HumanReview)
from .phase2 import (dev_handoff, dev_align, devwrite_criteria,
                     review_dev_criteria, dev_write_design,
                     dev_write_plan, dev_review_plan,
                     dev_git_init, dev_exec_step, dev_review_step,
                     dev_commit, dev_rollback, dev_escalate)
from .phase3 import qa_handoff, qa_align
from .flush import (master_flush_after_clarify, master_flush_after_pm,
                    master_flush_after_dev)
from .checkpoint import (resume_router, resume_pm_handoff,
                         resume_dev_handoff, resume_qa_handoff,
                         resume_dev_exec_step)

NODES = [
    interruptible(resume_router),
    interruptible(resume_pm_handoff), interruptible(resume_dev_handoff),
    interruptible(resume_qa_handoff), interruptible(resume_dev_exec_step),
    interruptible(dev_handoff), interruptible(dev_align),
    interruptible(devwrite_criteria), interruptible(review_dev_criteria),
    interruptible(dev_write_design), interruptible(dev_write_plan),
    interruptible(dev_review_plan),
    interruptible(dev_git_init), interruptible(dev_exec_step),
    interruptible(dev_review_step), interruptible(dev_commit),
    interruptible(dev_rollback), interruptible(dev_escalate),
    interruptible(qa_handoff), interruptible(qa_align),
    interruptible(master_flush_after_clarify),
    interruptible(master_flush_after_pm),
    interruptible(master_flush_after_dev),
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

    graph.set_entry_point("resume_router")

    # ── resume_router 路由 ──
    graph.add_conditional_edges("resume_router", lambda s: s.get("phase", ""), {
        "pre_flight": PreFlightClarify.entries["init"],
        "resume_pm_handoff": "resume_pm_handoff",
        "resume_dev_handoff": "resume_dev_handoff",
        "resume_qa_handoff": "resume_qa_handoff",
        "resume_dev_exec_step": "resume_dev_exec_step",
    })

    # ── resume 节点 → 实际工作节点 ──
    graph.add_edge("resume_pm_handoff", PMHandoff.entries["run"])
    graph.add_edge("resume_dev_handoff", "dev_handoff")
    graph.add_edge("resume_qa_handoff", "qa_handoff")
    graph.add_edge("resume_dev_exec_step", "dev_exec_step")

    # ── Phase 0 跨阶段边 ──
    graph.add_edge(PreFlightClarify.exits["close"], "master_flush_after_clarify")
    graph.add_edge("master_flush_after_clarify", PMHandoff.entries["run"])

    # ── Phase 1: PM 出方案 ──
    graph.add_edge(PMHandoff.exits["run"], PMAlign.entries["read"])
    graph.add_edge(PMAlign.exits["read"], MasterReplyPM.entries["run"])
    graph.add_edge(MasterReplyPM.exits["run"], JudgeMasterReply.entries["run"])
    graph.add_conditional_edges(JudgeMasterReply.exits["run"], lambda s: s.get("judge_result", ""), {
        "A": PMWriteCriteria.entries["run"],
        "B": PMAlign.entries["master_reply"],
        "C": ClarifyInject.entries["run"],
    })
    graph.add_edge(ClarifyInject.exits["run"], MasterReplyPM.entries["run"])
    graph.add_conditional_edges(PMWriteCriteria.exits["run"], lambda s: s.get("judge_result", ""), {
        "review_pm_criteria": ReviewPMCriteria.entries["run"],
        "pmwrite_criteria": PMWriteCriteria.entries["run"],
    })
    graph.add_conditional_edges(ReviewPMCriteria.exits["run"], lambda s: s.get("judge_result", ""), {
        "pm_write_doc": PMWriteDoc.entries["run"],
        "pmwrite_criteria": PMWriteCriteria.entries["run"],
    })
    graph.add_edge(PMWriteDoc.exits["run"], ReviewPMOutput.entries["run"])
    graph.add_conditional_edges(ReviewPMOutput.exits["run"], lambda s: s.get("judge_result", ""), {
        "human_review": HumanReview.entries["run"],
        "pm_write_doc": PMWriteDoc.entries["run"],
    })
    graph.add_conditional_edges(HumanReview.exits["run"], lambda s: s.get("judge_result", ""), {
        END: "master_flush_after_pm",
        "review_pm_output": ReviewPMOutput.entries["run"],
    })

    # ── Phase 2: Dev 出设计 + 编码执行 ──
    graph.add_edge("master_flush_after_pm", "dev_handoff")
    graph.add_edge("dev_handoff", "dev_align")
    graph.add_edge("dev_align", "devwrite_criteria")
    graph.add_conditional_edges("devwrite_criteria", lambda s: s.get("judge_result", ""), {
        "review_dev_criteria": "review_dev_criteria",
        "devwrite_criteria": "devwrite_criteria",
    })
    graph.add_conditional_edges("review_dev_criteria", lambda s: s.get("judge_result", ""), {
        "dev_write_design": "dev_write_design",
        "devwrite_criteria": "devwrite_criteria",
    })
    graph.add_edge("dev_write_design", "dev_write_plan")
    graph.add_edge("dev_write_plan", "dev_review_plan")
    graph.add_conditional_edges("dev_review_plan", lambda s: s.get("judge_result", ""), {
        "dev_exec": "dev_git_init",
        "dev_write_plan": "dev_write_plan",
    })
    graph.add_edge("dev_git_init", "dev_exec_step")
    graph.add_edge("dev_exec_step", "dev_review_step")
    graph.add_conditional_edges("dev_review_step", lambda s: s.get("judge_result", ""), {
        "dev_commit": "dev_commit",
        "step_retry": "dev_exec_step",
        "dev_rollback": "dev_rollback",
        "dev_escalate": "dev_escalate",
    })
    graph.add_conditional_edges("dev_commit", lambda s: s.get("judge_result", ""), {
        "dev_exec_step": "dev_exec_step",
        "done": "master_flush_after_dev",
    })
    graph.add_edge("dev_rollback", "dev_exec_step")
    graph.add_edge("dev_escalate", "dev_exec_step")

    # ── Phase 3: QA ──
    graph.add_edge("master_flush_after_dev", "qa_handoff")
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

    hotkey = runtime.config.get("interrupt_hotkey") or ""
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
