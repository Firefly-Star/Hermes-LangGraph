"""Graph — LangGraph 图构建与入口。"""
import os, sys

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .utils import WorkflowState, setup_runtime
from .phase0 import pre_flight_clarify
from .phase1 import (pm_handoff, pm_align, master_reply_pm,
                     judge_master_reply, clarify_inject,
                     pmwrite_criteria, review_pm_criteria,
                     pm_write_doc, review_pm_output, human_review)
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
    resume_router,
    resume_pm_handoff, resume_dev_handoff,
    resume_qa_handoff, resume_dev_exec_step,
    pre_flight_clarify,
    pm_handoff, pm_align, master_reply_pm, judge_master_reply, clarify_inject,
    pmwrite_criteria, review_pm_criteria, pm_write_doc, review_pm_output, human_review,
    dev_handoff, dev_align, devwrite_criteria, review_dev_criteria,
    dev_write_design, dev_write_plan, dev_review_plan,
    dev_git_init, dev_exec_step, dev_review_step, dev_commit, dev_rollback, dev_escalate,
    qa_handoff, qa_align,
    master_flush_after_clarify, master_flush_after_pm, master_flush_after_dev,
]


def build_graph(runtime) -> StateGraph:
    """构建 LangGraph StateGraph。"""
    for f in NODES:
        f._runtime = runtime

    graph = StateGraph(WorkflowState)

    # ── 注册所有节点 ──
    for f in NODES:
        graph.add_node(f.__name__, f)

    graph.set_entry_point("resume_router")

    # ── resume_router 路由 ──
    graph.add_conditional_edges("resume_router", lambda s: s.get("phase", ""), {
        "pre_flight": "pre_flight_clarify",
        "resume_pm_handoff": "resume_pm_handoff",
        "resume_dev_handoff": "resume_dev_handoff",
        "resume_qa_handoff": "resume_qa_handoff",
        "resume_dev_exec_step": "resume_dev_exec_step",
    })

    # ── resume 节点 → 实际工作节点 ──
    graph.add_edge("resume_pm_handoff", "pm_handoff")
    graph.add_edge("resume_dev_handoff", "dev_handoff")
    graph.add_edge("resume_qa_handoff", "qa_handoff")
    graph.add_edge("resume_dev_exec_step", "dev_exec_step")

    # ── Phase 0: 需求澄清 ──
    graph.add_edge("pre_flight_clarify", "master_flush_after_clarify")
    graph.add_edge("master_flush_after_clarify", "pm_handoff")

    # ── Phase 1: PM 出方案 ──
    graph.add_edge("pm_handoff", "pm_align")
    graph.add_edge("pm_align", "master_reply_pm")
    graph.add_edge("master_reply_pm", "judge_master_reply")
    graph.add_conditional_edges("judge_master_reply", lambda s: s.get("judge_result", ""), {
        "A": "pmwrite_criteria",
        "B": "pm_align",
        "C": "clarify_inject",
    })
    graph.add_edge("clarify_inject", "master_reply_pm")
    graph.add_conditional_edges("pmwrite_criteria", lambda s: s.get("judge_result", ""), {
        "review_pm_criteria": "review_pm_criteria",
        "pmwrite_criteria": "pmwrite_criteria",
    })
    graph.add_conditional_edges("review_pm_criteria", lambda s: s.get("judge_result", ""), {
        "pm_write_doc": "pm_write_doc",
        "pmwrite_criteria": "pmwrite_criteria",
    })
    graph.add_edge("pm_write_doc", "review_pm_output")
    graph.add_conditional_edges("review_pm_output", lambda s: s.get("judge_result", ""), {
        "human_review": "human_review",
        "pm_write_doc": "pm_write_doc",
    })
    graph.add_conditional_edges("human_review", lambda s: s.get("judge_result", ""), {
        END: "master_flush_after_pm",
        "review_pm_output": "review_pm_output",
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
    config = {"configurable": {"thread_id": "workflow-1"}}

    for event in app.stream(state, config):
        for node_name, node_state in event.items():
            if node_state is None:
                print(f"  [{node_name}] 完成")
                continue
            print(f"  [{node_name}] phase={node_state.get('phase', '?')}, "
                  f"judge={node_state.get('judge_result', '')[:20]}")

    print("\n✅ 框架就绪")
