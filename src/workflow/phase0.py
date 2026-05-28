"""Phase 0: 需求澄清。"""
import os

from .utils import WorkflowState, conv_name, call_agent, clarify_loop
from .config import MASTER_SYSTEM_PROMPT, ARTIFACTS_DIR


def pre_flight_clarify(state: WorkflowState) -> dict:
    """Phase 0: 交互式需求澄清。"""
    runtime = getattr(pre_flight_clarify, "_runtime", None)
    conv = conv_name("master")

    print(f"\n{'='*50}\n  ==> Phase 0: 需求澄清\n{'='*50}")

    for key in ["master_reply", "pm_reply_text", "pm_reply_path", "pmletter_path",
                "pm_criteria", "pm_criteria_self_check", "pm_criteria_path",
                "review_result", "human_feedback", "pm_align_round",
                "dev_conv", "devletter_path", "dev_feedback_path"]:
        runtime.context.set_ctx(key, "")

    artifacts_dir = os.path.join(runtime.runtime_dir, ARTIFACTS_DIR)
    os.makedirs(artifacts_dir, exist_ok=True)
    project_context_path = os.path.join(artifacts_dir, "project_context.md")

    runtime.logger.log_event("phase_started", detail="需求澄清")
    call_agent(runtime, "master", conv, MASTER_SYSTEM_PROMPT.format(workspace=runtime.workspace).strip())
    runtime.context.set_ctx("master_conv", conv)

    def _close(reason: str):
        call_agent(runtime, "master", conv,
                   "需求澄清阶段已结束。\n"
                   f"请将所有已确认的决策整理为完整的项目顶层决策记录，"
                   f"写入文件 {project_context_path}。\n"
                   "需包含：用户角色与权限、技术栈、功能范围、MVP 边界、"
                   "约束条件、页面结构等所有被确认过的信息。\n"
                   "勿包含对话中的口头语，只写正式的文档内容。\n"
                   "后续所有 agent 将通过这个文件了解项目。")
        runtime.logger.log_event("clarification_done", detail=reason)
        runtime.context.set_bg("project_context_path", project_context_path)
        runtime.context.set_bg("clarification", f"项目顶层决策文件：{project_context_path}")

    clarify_loop(runtime, conv, "== 需求澄清 ==", "请描述你的需求", _close)
    return {"phase": "done"}
