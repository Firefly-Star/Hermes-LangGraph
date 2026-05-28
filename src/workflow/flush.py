"""Master flush: phase 边界关闭旧对话、开新对话注入上下文。"""
import os

from .utils import WorkflowState, call_agent, ensure_write_file, open_master_conv
from .config import PHASES_DIR
from .checkpoint import save_checkpoint


def _master_flush(runtime, phase_name, next_phase_desc, resume_node,
                  phase_artifacts=""):
    """Master phase boundary flush: 写阶段总结 → 关旧对话 → 开新对话注入上下文。"""
    master_conv = runtime.context.get_ctx("master_conv")

    phases_dir = os.path.join(runtime.runtime_dir, PHASES_DIR)
    os.makedirs(phases_dir, exist_ok=True)
    summary_path = os.path.join(phases_dir, f"phase-summary-{phase_name}.md")
    call_agent(runtime, "master", master_conv,
        f"请将你刚完成的阶段总结写入 {summary_path}。格式如下：\n\n"
        "Summary:\n"
        f"1. Phase Completed:\n"
        f"   - 阶段：{phase_name}\n"
        "   - 核心产出物\n\n"
        "2. Key Decisions Made:\n"
        "   - 本阶段的关键决策\n\n"
        "3. Artifacts Produced:\n"
        "   - 文件清单（含路径）\n\n"
        "4. Open Issues / Risks:\n"
        "   - 遗留问题及风险\n\n"
        "5. Current Status:\n"
        f"   - 已完成: {phase_name}\n"
        f"   - 下一步: {next_phase_desc}\n\n"
        f"本阶段的实际产出文件（供撰写总结参考）：\n{phase_artifacts}")

    if not ensure_write_file(runtime, "master", master_conv, summary_path):
        call_agent(runtime, "master", master_conv,
                   f"将阶段总结写入文件 {summary_path}，使用 write_file 工具。")

    runtime.conversations.close("master", master_conv)

    new_conv = open_master_conv(runtime, summary_path)
    save_checkpoint(runtime, resume_node, phase_name, summary_path=summary_path)
    print(f"\n  ── Master flush: {phase_name} → {next_phase_desc} (新对话: {new_conv})")


def master_flush_after_clarify(state: WorkflowState) -> dict:
    """Phase 0→1 边界: flush Master，注入 project_context.md + clarify 总结。"""
    runtime = getattr(master_flush_after_clarify, "_runtime", None)
    artifacts = f"- {runtime.context.get_bg('project_context_path')}"
    _master_flush(runtime, "需求澄清", "PM 出方案", "pm_handoff",
                  phase_artifacts=artifacts)
    return {}


def master_flush_after_pm(state: WorkflowState) -> dict:
    """Phase 1→2 边界: flush Master，注入 project_context.md + PM 阶段总结。"""
    runtime = getattr(master_flush_after_pm, "_runtime", None)
    ws = runtime.workspace
    criteria = runtime.context.get_ctx("pm_criteria_path") or f"{ws}/criteria-pm.md"
    artifacts = (
        f"- {ws}/PM/PRD.md\n"
        f"- {ws}/PM/prototype.html\n"
        f"- {criteria}"
    )
    _master_flush(runtime, "PM 出方案", "Dev 实现", "dev_handoff",
                  phase_artifacts=artifacts)
    return {}


def master_flush_after_dev(state: WorkflowState) -> dict:
    """Phase 2→3 边界: flush Master，注入 project_context.md + Dev 阶段总结。"""
    runtime = getattr(master_flush_after_dev, "_runtime", None)
    ws = runtime.workspace
    artifacts = (
        f"- {ws}/Dev/design.md\n"
        f"- {ws}/Dev/plan.md\n"
        f"- {ws}/Dev/（代码仓库）"
    )
    _master_flush(runtime, "Dev 实现", "QA 对齐", "qa_handoff",
                  phase_artifacts=artifacts)
    return {}
