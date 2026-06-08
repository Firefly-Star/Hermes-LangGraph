"""Phase 0: 需求澄清。"""
import os

from .utils import conv_name, call_agent, register_nodes, clarify_loop
from .prompt import MASTER_SYSTEM_PROMPT


class PreFlightClarify:
    """Pre-flight setup + user ↔ Master interactive clarification."""

    entries = {"init": "pre_flight_init"}
    exits = {"close": "clarify_close"}

    _runtime = None

    @staticmethod
    def init(state) -> dict:
        """Setup context + init Master conversation (Call 1)."""
        runtime = PreFlightClarify._runtime
        conv = conv_name("master")

        runtime.msg.phase("Phase 0: 需求澄清")

        for key in ["master_reply", "pm_reply_text", "pm_reply_path", "pmletter_path",
                    "pm_criteria", "pm_criteria_self_check", "pm_criteria_path",
                    "review_result", "human_feedback", "pm_align_round",
                    "dev_conv", "devletter_path", "dev_feedback_path"]:
            runtime.context.set_ctx(key, "")

        artifacts_dir = runtime.paths.artifacts
        os.makedirs(artifacts_dir, exist_ok=True)
        project_context_path = os.path.join(artifacts_dir, "project_context.md")
        runtime.context.set_bg("project_context_path", project_context_path)

        runtime.logger.log_event("phase_started", detail="需求澄清")
        call_agent(runtime, "master", conv,
                   MASTER_SYSTEM_PROMPT.format(workspace=runtime.paths.workspace).strip())
        runtime.context.set_ctx("master_conv", conv)

        runtime.context.set_ctx("clarify_reason", "")

        return {"phase": "clarify_inject"}

    @staticmethod
    def clarify(state) -> dict:
        """User ↔ Master interactive clarification via clarify_loop."""
        runtime = PreFlightClarify._runtime
        master_conv = runtime.context.get_ctx("master_conv")

        reason = clarify_loop(runtime, master_conv, "== 需求澄清 ==", "请描述你的需求")
        runtime.context.set_ctx("clarify_reason", reason)
        runtime.logger.log_event("clarification_done", detail=reason)
        return {"phase": "clarify_close"}

    @staticmethod
    def close(state) -> dict:
        """Write project_context.md and finish (Call 2)."""
        runtime = PreFlightClarify._runtime
        conv = runtime.context.get_ctx("master_conv")
        reason = runtime.context.get_ctx("clarify_reason") or "用户确认完成"
        project_context_path = runtime.context.get_bg("project_context_path") or \
            os.path.join(runtime.paths.artifacts, "project_context.md")

        call_agent(runtime, "master", conv,
            "需求澄清阶段已结束。\n"
            f"请将所有已确认的决策整理为完整的项目顶层决策记录，"
            f"写入文件 {project_context_path}。\n"
            "需包含：用户角色与权限、技术栈、功能范围、MVP 边界、"
            "约束条件、页面结构等所有被确认过的信息。\n"
            "勿包含对话中的口头语，只写正式的文档内容。\n"
            "后续所有 agent 将通过这个文件了解项目。")

        runtime.context.set_bg("clarification", f"项目顶层决策文件：{project_context_path}")

        return {"phase": "done"}

    @classmethod
    def register(cls, graph, runtime):
        """Register nodes and intra-phase edges with LangGraph."""
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "pre_flight_init": cls.init,
            "pre_flight_clarify": cls.clarify,
            "clarify_close": cls.close,
        })

        # ── Intra-phase edges ──
        graph.add_edge("pre_flight_init", "pre_flight_clarify")
        graph.add_edge("pre_flight_clarify", "clarify_close")
