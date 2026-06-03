"""Master flush: phase 边界关闭旧对话、开新对话注入上下文。"""
import os

from .utils import call_agent, ensure_write_file, open_master_conv, register_nodes
from .checkpoint import save_checkpoint


class MasterFlushClarify:
    """Phase 0→1 边界: flush Master，2 节点。"""

    entries = {"write_summary": "master_flush_clarify_summary"}
    exits = {"flush_conv": "master_flush_clarify_conv"}

    _runtime = None

    @staticmethod
    def write_summary(state) -> dict:
        """写需求澄清阶段总结 (1 call_agent + ensure_write_file)."""
        runtime = MasterFlushClarify._runtime
        master_conv = runtime.context.get_ctx("master_conv")

        os.makedirs(runtime.paths.phases, exist_ok=True)
        summary_path = os.path.join(runtime.paths.phases, "phase-summary-需求澄清.md")
        artifacts = f"- {runtime.context.get_bg('project_context_path')}"

        call_agent(runtime, "master", master_conv,
            f"请将你刚完成的阶段总结写入 {summary_path}。格式如下：\n\n"
            "Summary:\n"
            "1. Phase Completed:\n"
            "   - 阶段：需求澄清\n"
            "   - 核心产出物\n\n"
            "2. Key Decisions Made:\n"
            "   - 本阶段的关键决策\n\n"
            "3. Artifacts Produced:\n"
            "   - 文件清单（含路径）\n\n"
            "4. Open Issues / Risks:\n"
            "   - 遗留问题及风险\n\n"
            "5. Current Status:\n"
            "   - 已完成: 需求澄清\n"
            "   - 下一步: PM 出方案\n\n"
            f"本阶段的实际产出文件（供撰写总结参考）：\n{artifacts}")

        if not ensure_write_file(runtime, "master", master_conv, summary_path):
            call_agent(runtime, "master", master_conv,
                       f"将阶段总结写入文件 {summary_path}，使用 write_file 工具。")

        runtime.context.set_ctx("phase_summary_path", summary_path)
        return {"phase": "clarify_flushed", "judge_result": ""}

    @staticmethod
    def flush_conv(state) -> dict:
        """关旧对话、开新对话、存 checkpoint (open_master_conv → 1 call_agent)."""
        runtime = MasterFlushClarify._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        summary_path = runtime.context.get_ctx("phase_summary_path")

        runtime.conversations.close("master", master_conv)
        new_conv = open_master_conv(runtime, summary_path)
        save_checkpoint(runtime, "pm_handoff", "需求澄清", summary_path=summary_path)
        print(f"\n  ── Master flush: 需求澄清 → PM 出方案 (新对话: {new_conv})")
        return {"phase": "clarify_conv_flushed", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "master_flush_clarify_summary": cls.write_summary,
            "master_flush_clarify_conv": cls.flush_conv,
        })
        graph.add_edge("master_flush_clarify_summary", "master_flush_clarify_conv")


class MasterFlushPM:
    """Phase 1→2 边界: flush Master，2 节点。"""

    entries = {"write_summary": "master_flush_pm_summary"}
    exits = {"flush_conv": "master_flush_pm_conv"}

    _runtime = None

    @staticmethod
    def write_summary(state) -> dict:
        """写 PM 出方案阶段总结 (1 call_agent + ensure_write_file)."""
        runtime = MasterFlushPM._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        ws = runtime.paths.workspace
        criteria = runtime.context.get_ctx("pm_criteria_path") or f"{ws}/criteria-pm.md"

        os.makedirs(runtime.paths.phases, exist_ok=True)
        summary_path = os.path.join(runtime.paths.phases, "phase-summary-PM出方案.md")
        artifacts = (
            f"- {ws}/PM/PRD.md\n"
            f"- {ws}/PM/prototype.html\n"
            f"- {criteria}"
        )

        call_agent(runtime, "master", master_conv,
            f"请将你刚完成的阶段总结写入 {summary_path}。格式如下：\n\n"
            "Summary:\n"
            "1. Phase Completed:\n"
            "   - 阶段：PM 出方案\n"
            "   - 核心产出物\n\n"
            "2. Key Decisions Made:\n"
            "   - 本阶段的关键决策\n\n"
            "3. Artifacts Produced:\n"
            "   - 文件清单（含路径）\n\n"
            "4. Open Issues / Risks:\n"
            "   - 遗留问题及风险\n\n"
            "5. Current Status:\n"
            "   - 已完成: PM 出方案\n"
            "   - 下一步: Dev 实现\n\n"
            f"本阶段的实际产出文件（供撰写总结参考）：\n{artifacts}")

        if not ensure_write_file(runtime, "master", master_conv, summary_path):
            call_agent(runtime, "master", master_conv,
                       f"将阶段总结写入文件 {summary_path}，使用 write_file 工具。")

        runtime.context.set_ctx("phase_summary_path", summary_path)
        return {"phase": "pm_flushed", "judge_result": ""}

    @staticmethod
    def flush_conv(state) -> dict:
        """关旧对话、开新对话、存 checkpoint (open_master_conv → 1 call_agent)."""
        runtime = MasterFlushPM._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        summary_path = runtime.context.get_ctx("phase_summary_path")

        runtime.conversations.close("master", master_conv)
        new_conv = open_master_conv(runtime, summary_path)
        save_checkpoint(runtime, "dev_handoff", "PM 出方案", summary_path=summary_path)
        print(f"\n  ── Master flush: PM 出方案 → Dev 实现 (新对话: {new_conv})")
        return {"phase": "pm_conv_flushed", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "master_flush_pm_summary": cls.write_summary,
            "master_flush_pm_conv": cls.flush_conv,
        })
        graph.add_edge("master_flush_pm_summary", "master_flush_pm_conv")


class MasterFlushDev:
    """Phase 2→3 边界: flush Master，2 节点。"""

    entries = {"write_summary": "master_flush_dev_summary"}
    exits = {"flush_conv": "master_flush_dev_conv"}

    _runtime = None

    @staticmethod
    def write_summary(state) -> dict:
        """写 Dev 实现阶段总结 (1 call_agent + ensure_write_file)."""
        runtime = MasterFlushDev._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        ws = runtime.paths.workspace

        os.makedirs(runtime.paths.phases, exist_ok=True)
        summary_path = os.path.join(runtime.paths.phases, "phase-summary-Dev实现.md")
        artifacts = (
            f"- {ws}/Dev/design.md\n"
            f"- {ws}/Dev/plan.md\n"
            f"- {ws}/Dev/（代码仓库）"
        )

        call_agent(runtime, "master", master_conv,
            f"请将你刚完成的阶段总结写入 {summary_path}。格式如下：\n\n"
            "Summary:\n"
            "1. Phase Completed:\n"
            "   - 阶段：Dev 实现\n"
            "   - 核心产出物\n\n"
            "2. Key Decisions Made:\n"
            "   - 本阶段的关键决策\n\n"
            "3. Artifacts Produced:\n"
            "   - 文件清单（含路径）\n\n"
            "4. Open Issues / Risks:\n"
            "   - 遗留问题及风险\n\n"
            "5. Current Status:\n"
            "   - 已完成: Dev 实现\n"
            "   - 下一步: QA 对齐\n\n"
            f"本阶段的实际产出文件（供撰写总结参考）：\n{artifacts}")

        if not ensure_write_file(runtime, "master", master_conv, summary_path):
            call_agent(runtime, "master", master_conv,
                       f"将阶段总结写入文件 {summary_path}，使用 write_file 工具。")

        runtime.context.set_ctx("phase_summary_path", summary_path)
        return {"phase": "dev_flushed", "judge_result": ""}

    @staticmethod
    def flush_conv(state) -> dict:
        """关旧对话、开新对话、存 checkpoint (open_master_conv → 1 call_agent)."""
        runtime = MasterFlushDev._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        summary_path = runtime.context.get_ctx("phase_summary_path")

        runtime.conversations.close("master", master_conv)
        new_conv = open_master_conv(runtime, summary_path)
        save_checkpoint(runtime, "qa_handoff", "Dev 实现", summary_path=summary_path)
        print(f"\n  ── Master flush: Dev 实现 → QA 对齐 (新对话: {new_conv})")
        return {"phase": "dev_conv_flushed", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "master_flush_dev_summary": cls.write_summary,
            "master_flush_dev_conv": cls.flush_conv,
        })
        graph.add_edge("master_flush_dev_summary", "master_flush_dev_conv")


class MasterFlushQA:
    """Phase 3→END 边界: flush Master，2 节点。"""

    entries = {"write_summary": "master_flush_qa_summary"}
    exits = {"flush_conv": "master_flush_qa_conv"}

    _runtime = None

    @staticmethod
    def write_summary(state) -> dict:
        """写 QA 测试阶段总结 (1 call_agent + ensure_write_file)."""
        runtime = MasterFlushQA._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        ws = runtime.paths.workspace

        os.makedirs(runtime.paths.phases, exist_ok=True)
        summary_path = os.path.join(runtime.paths.phases, "phase-summary-QA测试.md")
        artifacts = (
            f"- {ws}/QA/test-plan.md\n"
            f"- {ws}/QA/tests/\n"
            f"- {ws}/QA/test-report.md"
        )

        call_agent(runtime, "master", master_conv,
            f"请将你刚完成的阶段总结写入 {summary_path}。格式如下：\n\n"
            "Summary:\n"
            "1. Phase Completed:\n"
            "   - 阶段：QA 测试\n"
            "   - 核心产出物\n\n"
            "2. Key Decisions Made:\n"
            "   - 本阶段的关键决策\n\n"
            "3. Artifacts Produced:\n"
            "   - 文件清单（含路径）\n\n"
            "4. Open Issues / Risks:\n"
            "   - 遗留问题及风险\n\n"
            "5. Current Status:\n"
            "   - 已完成: QA 测试\n"
            "   - 下一步: 项目完成\n\n"
            f"本阶段的实际产出文件（供撰写总结参考）：\n{artifacts}")

        if not ensure_write_file(runtime, "master", master_conv, summary_path):
            call_agent(runtime, "master", master_conv,
                       f"将阶段总结写入文件 {summary_path}，使用 write_file 工具。")

        runtime.context.set_ctx("phase_summary_path", summary_path)
        return {"phase": "qa_flushed", "judge_result": ""}

    @staticmethod
    def flush_conv(state) -> dict:
        """关旧对话、开新对话、存 checkpoint (open_master_conv → 1 call_agent)."""
        runtime = MasterFlushQA._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        summary_path = runtime.context.get_ctx("phase_summary_path")

        runtime.conversations.close("master", master_conv)
        new_conv = open_master_conv(runtime, summary_path)
        save_checkpoint(runtime, "consistency_audit", "QA 测试", summary_path=summary_path)
        print(f"\n  ── Master flush: QA 测试完成 (新对话: {new_conv})")
        return {"phase": "qa_conv_flushed", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "master_flush_qa_summary": cls.write_summary,
            "master_flush_qa_conv": cls.flush_conv,
        })
        graph.add_edge("master_flush_qa_summary", "master_flush_qa_conv")
