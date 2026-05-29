"""Phase 0: 需求澄清。"""
import os

from .utils import conv_name, call_agent, judge_reply, interruptible, register_nodes
from .config import MASTER_SYSTEM_PROMPT, ARTIFACTS_DIR


class PreFlightClarify:
    """原 pre_flight_clarify 拆分为单 call_agent 节点后的逻辑分组。"""

    entries = {"init": "pre_flight_init"}
    exits = {"close": "clarify_close"}

    _runtime = None

    @staticmethod
    def init(state) -> dict:
        """Setup context + init Master conversation (Call 1)."""
        runtime = PreFlightClarify._runtime
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
        runtime.context.set_bg("project_context_path", project_context_path)

        runtime.logger.log_event("phase_started", detail="需求澄清")
        call_agent(runtime, "master", conv,
                   MASTER_SYSTEM_PROMPT.format(workspace=runtime.workspace).strip())
        runtime.context.set_ctx("master_conv", conv)

        runtime.context.set_ctx("clarify_round", "0")
        runtime.context.set_ctx("clarify_reason", "")

        return {"phase": "clarify_ask"}

    @staticmethod
    def ask(state) -> dict:
        """Wait for user input. EOF → close, otherwise → master_reply."""
        runtime = PreFlightClarify._runtime
        round_num = int(runtime.context.get_ctx("clarify_round") or "0") + 1
        runtime.context.set_ctx("clarify_round", str(round_num))

        end_word = runtime.config.get("input_end_word") or None
        hint = "请描述你的需求" if round_num == 1 \
            else "请回答 Master 的疑问，或直接 EOF 结束："

        cp = runtime.checkpoint.wait(
            "== 需求澄清 ==", hint,
            prompt="> ", end_word=end_word,
        )
        user_input = cp.message.strip()
        if not user_input:
            runtime.context.set_ctx("clarify_reason", "用户直接确认")
            return {"phase": "clarify_close"}

        runtime.context.set_ctx("clarify_user_input", user_input)
        return {"phase": "clarify_master_reply"}

    @staticmethod
    def master_reply(state) -> dict:
        """Master processes user input (Call 2)."""
        runtime = PreFlightClarify._runtime
        conv = runtime.context.get_ctx("master_conv")
        user_input = runtime.context.get_ctx("clarify_user_input")

        reply = call_agent(runtime, "master", conv,
            f"{user_input}\n不要产出任何东西，也不要修改任何文件，只需要说出你的理解，以及对有疑问的地方提出问题。")
        runtime.context.set_ctx("clarify_master_reply", reply)
        return {"phase": "clarify_judge"}

    @staticmethod
    def judge(state) -> dict:
        """Judge evaluates Master's understanding (Call 3 via judge_reply)."""
        runtime = PreFlightClarify._runtime
        reply = runtime.context.get_ctx("clarify_master_reply")

        result = judge_reply(runtime, "Master", reply, [
            "A. 需求已明确，可以进入下一阶段",
            "B. Master 有疑问需要用户继续回答",
        ], "judge-clarify")

        if result == "A":
            return {"phase": "clarify_confirm", "judge_result": "A"}
        else:
            return {"phase": "clarify_ask", "judge_result": "B"}

    @staticmethod
    def confirm(state) -> dict:
        """User confirms Master's understanding. EOF → close, else → correct."""
        runtime = PreFlightClarify._runtime
        end_word = runtime.config.get("input_end_word") or None

        cp = runtime.checkpoint.wait(
            "== 需求澄清 == (确认)",
            "Master 已确认理解需求。认可的话直接 EOF 进入下一阶段；不认可则说明哪里不对：",
            prompt="输入内容后按 Enter：", end_word=end_word,
        )
        confirm_input = cp.message.strip()
        if not confirm_input:
            runtime.context.set_ctx("clarify_reason", "用户确认 Master 理解正确")
            return {"phase": "clarify_close"}

        runtime.context.set_ctx("clarify_user_input", confirm_input)
        return {"phase": "clarify_correct"}

    @staticmethod
    def correct(state) -> dict:
        """Master receives user correction (Call 4)."""
        runtime = PreFlightClarify._runtime
        conv = runtime.context.get_ctx("master_conv")
        correction = runtime.context.get_ctx("clarify_user_input")

        call_agent(runtime, "master", conv,
            f"用户认为你的理解有偏差，请重新理解需求：\n{correction}")
        return {"phase": "clarify_judge"}

    @staticmethod
    def close(state) -> dict:
        """Write project_context.md and finish (Call 5)."""
        runtime = PreFlightClarify._runtime
        conv = runtime.context.get_ctx("master_conv")
        reason = runtime.context.get_ctx("clarify_reason") or "用户确认完成"
        project_context_path = runtime.context.get_bg("project_context_path") or \
            os.path.join(runtime.runtime_dir, ARTIFACTS_DIR, "project_context.md")

        call_agent(runtime, "master", conv,
            "需求澄清阶段已结束。\n"
            f"请将所有已确认的决策整理为完整的项目顶层决策记录，"
            f"写入文件 {project_context_path}。\n"
            "需包含：用户角色与权限、技术栈、功能范围、MVP 边界、"
            "约束条件、页面结构等所有被确认过的信息。\n"
            "勿包含对话中的口头语，只写正式的文档内容。\n"
            "后续所有 agent 将通过这个文件了解项目。")

        runtime.logger.log_event("clarification_done", detail=reason)
        runtime.context.set_bg("clarification", f"项目顶层决策文件：{project_context_path}")

        return {"phase": "done"}

    @classmethod
    def register(cls, graph, runtime):
        """Register nodes and intra-phase edges with LangGraph."""
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "pre_flight_init": cls.init,
            "clarify_ask": cls.ask,
            "clarify_master_reply": cls.master_reply,
            "clarify_judge": cls.judge,
            "clarify_confirm": cls.confirm,
            "clarify_correct": cls.correct,
            "clarify_close": cls.close,
        })

        # ── Intra-phase edges ──
        graph.add_edge("pre_flight_init", "clarify_ask")
        graph.add_conditional_edges("clarify_ask", lambda s: s.get("phase", ""), {
            "clarify_master_reply": "clarify_master_reply",
            "clarify_close": "clarify_close",
        })
        graph.add_edge("clarify_master_reply", "clarify_judge")
        graph.add_conditional_edges("clarify_judge", lambda s: s.get("judge_result", ""), {
            "A": "clarify_confirm",
            "B": "clarify_ask",
        })
        graph.add_conditional_edges("clarify_confirm", lambda s: s.get("phase", ""), {
            "clarify_close": "clarify_close",
            "clarify_correct": "clarify_correct",
        })
        graph.add_edge("clarify_correct", "clarify_judge")
